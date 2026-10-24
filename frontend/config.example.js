// Copy to config.js and fill in your Cesium ion token (https://ion.cesium.com/tokens).
window.APP_CONFIG = {
  CESIUM_ION_TOKEN: "",
  // Backend base URL. "" = same origin (when served by the FastAPI app on :8000).
  // Use "http://localhost:8000" if you serve the frontend separately.
  API_BASE: "",
  // Used only if the backend is unreachable (demo-route mode).
  FALLBACK_BBOX: [-116.89, 32.94, -116.56, 33.21],
};
