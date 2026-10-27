// Mode-aware least-cost search for gen-tie routing.
//
// Search state = (cell, mode, run length since the last mode switch). Mode 0 = overhead, 1 = underground.
//   move   (a, m, k) -> (b, m, min(k + inc, K))   cost (w_m[a] + w_m[b]) / 2 * step length   (8-connected)
//   switch (a, m, K) -> (a, 1-m, 0)               cost T (transition structure), only once the run is >= K
// Run length counts in units of `unit_m`: a straight step adds round(cell/unit), a diagonal floor(cell*sqrt2/unit),
// so diagonals are under-counted and every accepted run is at least min_run_m long. The goal must be reached
// with k == K (the last run is long enough too); routes with no switch at all are exempt, which a second,
// switch-free search covers. With min_run_m = 0 the state is just (cell, mode).
//
// Search: Dijkstra or A* with a monotone radix heap (or std::priority_queue, for comparison). Heuristics are
// lower bounds on the free-switching cost - each step at min(w_0, w_1), no transitions - which is never more
// than the true cost: octile distance x cheapest weight, or ALT (landmark distances on the free-switching grid,
// |d_L(goal) - d_L(cell)|). A landmark at the goal makes the ALT bound the exact free-switching distance.
//
// Memory: one float32 cost and one uint16 parent code per state, allocated once per engine and reset by
// walking the states a search touched. Parent code = move direction (0-7) or 8 for a switch, the previous
// run-length bucket, and a closed flag in the top bit.
//
// C ABI (ctypes), no exceptions across the boundary. Build: python -m engine.build

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <limits>
#include <new>
#include <queue>
#include <utility>
#include <vector>

#if defined(_WIN32)
#define GT_API extern "C" __declspec(dllexport)
#else
#define GT_API extern "C" __attribute__((visibility("default")))
#endif

namespace {

constexpr float INF = std::numeric_limits<float>::infinity();
constexpr uint16_t NO_PARENT = 0x7FFF;
constexpr uint16_t CLOSED = 0x8000;
constexpr int SWITCH = 8;
constexpr int MAX_K = 2047;  // 11 bits of the parent code
constexpr float H_GUARD = 0.99999f;  // keeps float-rounded lower bounds below the true cost

constexpr int DR[8] = {-1, -1, -1, 0, 0, 1, 1, 1};
constexpr int DC[8] = {-1, 0, 1, -1, 1, -1, 0, 1};
constexpr bool DIAG[8] = {true, false, true, false, false, true, false, true};

uint32_t float_bits(float f) { uint32_t u; std::memcpy(&u, &f, 4); return u; }
float bits_float(uint32_t u) { float f; std::memcpy(&f, &u, 4); return f; }

// Monotone priority queue. Non-negative IEEE-754 floats order like their uint32 bit patterns, so the
// keys can go straight into a radix heap (Dijkstra keys never decrease; neither do A* keys with a
// consistent heuristic, up to float rounding, which push() clamps).
class RadixHeap {
 public:
  void clear() { for (auto& b : buckets_) b.clear(); size_ = 0; last_ = 0; }
  bool empty() const { return size_ == 0; }
  void push(float key, uint32_t v) {
    uint32_t k = std::max(float_bits(key), last_);
    buckets_[index(k)].push_back({k, v});
    ++size_;
  }
  std::pair<float, uint32_t> pop() {
    if (buckets_[0].empty()) {
      int i = 1;
      while (buckets_[i].empty()) ++i;
      uint32_t mn = UINT32_MAX;
      for (const auto& e : buckets_[i]) mn = std::min(mn, e.first);
      last_ = mn;
      for (const auto& e : buckets_[i]) buckets_[index(e.first)].push_back(e);
      buckets_[i].clear();
    }
    auto e = buckets_[0].back();
    buckets_[0].pop_back();
    --size_;
    return {bits_float(e.first), e.second};
  }

 private:
  int index(uint32_t k) const { return k == last_ ? 0 : 32 - __builtin_clz(k ^ last_); }
  std::vector<std::pair<uint32_t, uint32_t>> buckets_[33];
  size_t size_ = 0;
  uint32_t last_ = 0;
};

class BinaryHeap {
 public:
  void clear() { q_ = {}; }
  bool empty() const { return q_.empty(); }
  void push(float key, uint32_t v) { q_.push({key, v}); }
  std::pair<float, uint32_t> pop() { auto e = q_.top(); q_.pop(); return e; }

 private:
  using E = std::pair<float, uint32_t>;
  std::priority_queue<E, std::vector<E>, std::greater<E>> q_;
};

struct Engine {
  int R = 0, C = 0, layers = 1;
  size_t N = 0;
  double cell = 30.0;
  std::vector<float> w[2], wmin;
  float wmin_global = 0.f;
  std::vector<std::vector<float>> landmarks;
  // search buffers, sized for the largest state space used so far
  std::vector<float> dist;
  std::vector<uint16_t> par;
  std::vector<uint32_t> touched;
  std::vector<float> hcache;
  std::vector<uint32_t> htouched;
  RadixHeap radix;
  BinaryHeap binary;
};

struct Query {
  int32_t src_r, src_c, dst_r, dst_c;
  double transition_cost;
  double min_run_m;   // 0 = no minimum run length
  double unit_m;      // run-length resolution
  int32_t heuristic;  // 0 none (Dijkstra), 1 octile, 2 octile + ALT landmarks
  int32_t heap;       // 0 radix, 1 binary
};

struct Result {
  double cost;
  int32_t path_len;
  int32_t status;  // 0 ok, 1 no path, 2 path buffer too small, 3 bad arguments, 4 out of memory
  int64_t expanded;
  int64_t pushed;
};

struct Search {  // one search configuration
  int K, S, inc[8];
  bool allow_switch;
  float T;
};

float heuristic(Engine& e, int mode, size_t cell, size_t goal) {
  if (mode == 0) return 0.f;
  float h = e.hcache[cell];
  if (!std::isnan(h)) return h;
  double dr = std::abs(double(cell / e.C) - double(goal / e.C));
  double dc = std::abs(double(cell % e.C) - double(goal % e.C));
  double oct = (std::max(dr, dc) + (std::sqrt(2.0) - 1.0) * std::min(dr, dc)) * e.cell * e.wmin_global;
  h = float(oct);
  if (mode == 2)
    for (const auto& L : e.landmarks) h = std::max(h, std::abs(L[goal] - L[cell]));
  h *= H_GUARD;
  e.hcache[cell] = h;
  e.htouched.push_back(uint32_t(cell));
  return h;
}

void reset(Engine& e) {
  for (uint32_t t : e.touched) { e.dist[t] = INF; e.par[t] = NO_PARENT; }
  e.touched.clear();
  for (uint32_t c : e.htouched) e.hcache[c] = std::numeric_limits<float>::quiet_NaN();
  e.htouched.clear();
}

// Returns the goal state or -1. Leaves dist/par filled for path reconstruction; caller resets.
template <class Heap>
int64_t run(Engine& e, Heap& heap, const Search& s, size_t src, size_t goal, int hmode, Result& res) {
  heap.clear();
  const int K1 = s.K + 1;
  auto push = [&](uint32_t t, float g, uint16_t code, size_t cell) {
    if (e.dist[t] == INF) e.touched.push_back(t);
    e.dist[t] = g;
    e.par[t] = code;
    heap.push(g + heuristic(e, hmode, cell, goal), t);
    ++res.pushed;
  };
  for (int m = 0; m < e.layers; ++m) push(uint32_t(src * s.S + m * K1), 0.f, NO_PARENT, src);

  while (!heap.empty()) {
    uint32_t st = heap.pop().second;
    if (e.par[st] & CLOSED) continue;
    e.par[st] |= CLOSED;
    ++res.expanded;
    const size_t cell = st / s.S;
    const int rem = int(st % s.S), m = rem / K1, k = rem % K1;
    if (cell == goal && k == s.K) return st;
    const float g = e.dist[st];
    const float* wm = e.w[m].data();
    const int r = int(cell / e.C), c = int(cell % e.C);
    for (int d = 0; d < 8; ++d) {
      const int nr = r + DR[d], nc = c + DC[d];
      if (nr < 0 || nr >= e.R || nc < 0 || nc >= e.C) continue;
      const size_t v = size_t(nr) * e.C + nc;
      const int nk = std::min(k + s.inc[d], s.K);
      const uint32_t t = uint32_t(v * s.S + m * K1 + nk);
      if (e.par[t] & CLOSED) continue;
      const float ng = g + (wm[cell] + wm[v]) * 0.5f * float(DIAG[d] ? e.cell * std::sqrt(2.0) : e.cell);
      if (ng < e.dist[t]) push(t, ng, uint16_t(d | (k << 4)), v);
    }
    if (s.allow_switch && e.layers == 2 && k == s.K) {
      const uint32_t t = uint32_t(cell * s.S + (1 - m) * K1);
      const float ng = g + s.T;
      if (!(e.par[t] & CLOSED) && ng < e.dist[t]) push(t, ng, uint16_t(SWITCH | (k << 4)), cell);
    }
  }
  return -1;
}

// Walks parents back from the goal. Cells keep the mode they were reached in (a switch shows up as a
// change of mode between consecutive cells).
int reconstruct(const Engine& e, const Search& s, int64_t goal_state, int32_t* cells, int8_t* modes, int cap) {
  const int K1 = s.K + 1;
  std::vector<std::pair<int32_t, int8_t>> out;
  uint32_t st = uint32_t(goal_state);
  while (true) {
    const size_t cell = st / s.S;
    const int m = int(st % s.S) / K1;
    if (!out.empty() && out.back().first == int32_t(cell)) out.back().second = int8_t(m);
    else out.push_back({int32_t(cell), int8_t(m)});
    const uint16_t code = e.par[st] & ~CLOSED;
    if (code == NO_PARENT) break;
    const int mv = code & 15, pk = code >> 4;
    if (mv == SWITCH) {
      st = uint32_t(cell * s.S + (1 - m) * K1 + pk);
    } else {
      const size_t prev = size_t(int(cell / e.C) - DR[mv]) * e.C + size_t(int(cell % e.C) - DC[mv]);
      st = uint32_t(prev * s.S + m * K1 + pk);
    }
  }
  if (int(out.size()) > cap) return -int(out.size());
  std::reverse(out.begin(), out.end());
  for (size_t i = 0; i < out.size(); ++i) { cells[i] = out[i].first; modes[i] = out[i].second; }
  return int(out.size());
}

bool ensure(Engine& e, size_t states) {
  try {
    if (e.dist.size() < states) {
      e.dist.assign(states, INF);
      e.par.assign(states, NO_PARENT);
      e.touched.clear();
    }
    if (e.hcache.size() != e.N) e.hcache.assign(e.N, std::numeric_limits<float>::quiet_NaN());
  } catch (const std::bad_alloc&) {
    return false;
  }
  return true;
}

Search make_search(const Engine& e, double cell, double min_run_m, double unit_m, bool allow_switch, double T) {
  Search s{};
  s.allow_switch = allow_switch;
  s.T = float(T);
  s.K = (allow_switch && min_run_m > 0) ? int(std::ceil(min_run_m / unit_m - 1e-9)) : 0;
  for (int d = 0; d < 8; ++d)
    s.inc[d] = s.K ? int(DIAG[d] ? std::floor(cell * std::sqrt(2.0) / unit_m) : std::lround(cell / unit_m)) : 0;
  s.S = e.layers * (s.K + 1);
  return s;
}

}  // namespace

GT_API void* gt_create(int32_t rows, int32_t cols, const float* w_oh, const float* w_ug, double cell_m) {
  Engine* e = new (std::nothrow) Engine();
  if (!e) return nullptr;
  try {
    e->R = rows; e->C = cols; e->N = size_t(rows) * cols; e->cell = cell_m;
    e->layers = w_ug ? 2 : 1;
    e->w[0].assign(w_oh, w_oh + e->N);
    if (w_ug) e->w[1].assign(w_ug, w_ug + e->N);
    e->wmin = e->w[0];
    if (w_ug) for (size_t i = 0; i < e->N; ++i) e->wmin[i] = std::min(e->w[0][i], e->w[1][i]);
    e->wmin_global = *std::min_element(e->wmin.begin(), e->wmin.end());
  } catch (const std::bad_alloc&) {
    delete e;
    return nullptr;
  }
  return e;
}

GT_API void gt_destroy(void* h) { delete static_cast<Engine*>(h); }

// Single-source costs on the free-switching grid (min(w_0, w_1), no transitions) into out[N]:
// the same quantity scikit-image's MCP_Geometric.find_costs computes (there in cell units).
GT_API int32_t gt_tree(void* h, int32_t root_r, int32_t root_c, int32_t heap_kind, float* out, int64_t* expanded) {
  Engine& e = *static_cast<Engine*>(h);
  if (root_r < 0 || root_r >= e.R || root_c < 0 || root_c >= e.C) return 3;
  std::vector<uint8_t> closed;
  try { closed.assign(e.N, 0); } catch (const std::bad_alloc&) { return 4; }
  std::fill(out, out + e.N, INF);
  auto body = [&](auto& heap) {
    heap.clear();
    const size_t root = size_t(root_r) * e.C + root_c;
    out[root] = 0.f;
    heap.push(0.f, uint32_t(root));
    int64_t n = 0;
    const float* w = e.wmin.data();
    while (!heap.empty()) {
      const uint32_t u = heap.pop().second;
      if (closed[u]) continue;
      closed[u] = 1;
      ++n;
      const float g = out[u];
      const int r = int(u / e.C), c = int(u % e.C);
      for (int d = 0; d < 8; ++d) {
        const int nr = r + DR[d], nc = c + DC[d];
        if (nr < 0 || nr >= e.R || nc < 0 || nc >= e.C) continue;
        const size_t v = size_t(nr) * e.C + nc;
        if (closed[v]) continue;
        const float ng = g + (w[u] + w[v]) * 0.5f * float(DIAG[d] ? e.cell * std::sqrt(2.0) : e.cell);
        if (ng < out[v]) { out[v] = ng; heap.push(ng, uint32_t(v)); }
      }
    }
    return n;
  };
  const int64_t n = heap_kind == 1 ? body(e.binary) : body(e.radix);
  if (expanded) *expanded = n;
  return 0;
}

GT_API int32_t gt_set_landmarks(void* h, int32_t n, const float* dists) {
  Engine& e = *static_cast<Engine*>(h);
  try {
    e.landmarks.assign(n, {});
    for (int i = 0; i < n; ++i) e.landmarks[i].assign(dists + size_t(i) * e.N, dists + size_t(i + 1) * e.N);
  } catch (const std::bad_alloc&) {
    return 4;
  }
  return 0;
}

// Point-to-point search. With min_run_m > 0 this runs the constrained search and a switch-free search
// (a single run of either mode, exempt from the minimum) and keeps the cheaper; expanded/pushed cover both.
GT_API int32_t gt_route(void* h, const Query* q, Result* res, int32_t* path_cells, int8_t* path_modes, int32_t cap) {
  Engine& e = *static_cast<Engine*>(h);
  *res = Result{INF, 0, 0, 0, 0};
  if (q->src_r < 0 || q->src_r >= e.R || q->src_c < 0 || q->src_c >= e.C || q->dst_r < 0 || q->dst_r >= e.R ||
      q->dst_c < 0 || q->dst_c >= e.C || q->unit_m <= 0 || q->heuristic < 0 || q->heuristic > 2) {
    res->status = 3;
    return 3;
  }
  const size_t src = size_t(q->src_r) * e.C + q->src_c, goal = size_t(q->dst_r) * e.C + q->dst_c;
  std::vector<Search> searches = {make_search(e, e.cell, q->min_run_m, q->unit_m, true, q->transition_cost)};
  if (searches[0].K > MAX_K) { res->status = 3; return 3; }
  if (searches[0].K > 0) searches.push_back(make_search(e, e.cell, 0, q->unit_m, false, q->transition_cost));

  int32_t best_len = 0;
  for (const Search& s : searches) {
    if (!ensure(e, e.N * size_t(s.S))) { res->status = 4; return 4; }
    const int64_t goal_state = q->heap == 1 ? run(e, e.binary, s, src, goal, q->heuristic, *res)
                                            : run(e, e.radix, s, src, goal, q->heuristic, *res);
    if (goal_state >= 0 && e.dist[goal_state] < res->cost) {
      res->cost = e.dist[goal_state];
      best_len = reconstruct(e, s, goal_state, path_cells, path_modes, cap);
    }
    reset(e);
  }
  if (res->cost == INF) { res->status = 1; return 1; }
  if (best_len < 0) { res->status = 2; res->path_len = -best_len; return 2; }
  res->path_len = best_len;
  return 0;
}
