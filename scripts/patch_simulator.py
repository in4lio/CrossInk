from pathlib import Path

Import("env")  # noqa: F821 - SCons injects this at build time


def replace_once(path: Path, old: str, new: str, warn: bool = True) -> bool:
    text = path.read_text()
    if new in text:
        return False
    if old not in text:
        if warn:
            print(f"patch_simulator: expected text not found in {path}")
        return False
    path.write_text(text.replace(old, new, 1))
    return True


def insert_before_once(path: Path, marker: str, insertion: str, warn: bool = True) -> bool:
    text = path.read_text()
    if insertion in text:
        return False
    if marker not in text:
        if warn:
            print(f"patch_simulator: expected insertion marker not found in {path}")
        return False
    path.write_text(text.replace(marker, insertion + "\n" + marker, 1))
    return True


def patch_simulator(*_args, **_kwargs):
    libdeps_dir = Path(env.subst("$PROJECT_LIBDEPS_DIR"))
    pioenv = env.subst("$PIOENV")
    hal_display = libdeps_dir / pioenv / "simulator" / "src" / "HalDisplay.cpp"
    hal_display_header = libdeps_dir / pioenv / "simulator" / "src" / "HalDisplay.h"
    esp_http_client = libdeps_dir / pioenv / "simulator" / "src" / "esp_http_client.h"
    hal_tilt_sensor = libdeps_dir / pioenv / "simulator" / "src" / "HalTiltSensor.h"
    http_client = libdeps_dir / pioenv / "simulator" / "src" / "HTTPClient.h"
    network_client = libdeps_dir / pioenv / "simulator" / "src" / "NetworkClient.h"
    stream = libdeps_dir / pioenv / "simulator" / "src" / "Stream.h"

    if not hal_display.exists():
        print(f"patch_simulator: simulator dependency not found yet for {pioenv}")
        return

    text = hal_display.read_text()
    if "displayedBwBuffer" in text and "displayGrayBuffer(bool, const unsigned char *, bool) {}" not in text:
        pass

    changed = False
    changed |= replace_once(
        hal_display,
        """static uint32_t
    pixelBuf[HalDisplay::DISPLAY_WIDTH * HalDisplay::DISPLAY_HEIGHT];
static std::atomic<bool> pendingPresent{false};""",
        """static uint32_t
    pixelBuf[HalDisplay::DISPLAY_WIDTH * HalDisplay::DISPLAY_HEIGHT];
static uint8_t displayedBwBuffer[HalDisplay::BUFFER_SIZE];
static uint8_t grayscaleLsbBuffer[HalDisplay::BUFFER_SIZE];
static uint8_t grayscaleMsbBuffer[HalDisplay::BUFFER_SIZE];
static bool displayedBwValid = false;
static bool grayscaleLsbValid = false;
static bool grayscaleMsbValid = false;
static std::atomic<bool> pendingPresent{false};""",
    )
    changed |= replace_once(
        hal_display,
        """void HalDisplay::refreshDisplay(RefreshMode /*mode*/, bool /*turnOffScreen*/) {
  const uint8_t *fb = getFrameBuffer();
  for (int y = 0; y < DISPLAY_HEIGHT; y++) {""",
        """void HalDisplay::refreshDisplay(RefreshMode /*mode*/, bool /*turnOffScreen*/) {
  const uint8_t *fb = getFrameBuffer();
  memcpy(displayedBwBuffer, fb, BUFFER_SIZE);
  displayedBwValid = true;
  for (int y = 0; y < DISPLAY_HEIGHT; y++) {""",
    )
    changed |= replace_once(
        hal_display,
        """bool HalDisplay::shouldQuit() const { return quitRequested.load(); }

void HalDisplay::deepSleep() { presentIfNeeded(); }""",
        """bool HalDisplay::shouldQuit() const { return quitRequested.load(); }

static bool framebufferBitIsWhite(const uint8_t *fb, int x, int y) {
  const int byteIdx = (y * HalDisplay::DISPLAY_WIDTH + x) / 8;
  const int bitIdx = 7 - (x % 8);
  return (fb[byteIdx] & (1 << bitIdx)) != 0;
}

void HalDisplay::deepSleep() { presentIfNeeded(); }""",
    )
    changed |= replace_once(
        hal_display,
        """void HalDisplay::copyGrayscaleBuffers(const uint8_t *, const uint8_t *) {}
void HalDisplay::copyGrayscaleLsbBuffers(const uint8_t *) {}
void HalDisplay::copyGrayscaleMsbBuffers(const uint8_t *) {}
void HalDisplay::cleanupGrayscaleBuffers(const uint8_t *) {}
void HalDisplay::displayGrayBuffer(bool, const unsigned char *, bool) {}""",
        """void HalDisplay::copyGrayscaleBuffers(const uint8_t *lsbBuffer, const uint8_t *msbBuffer) {
  if (lsbBuffer) {
    memcpy(grayscaleLsbBuffer, lsbBuffer, BUFFER_SIZE);
    grayscaleLsbValid = true;
  }
  if (msbBuffer) {
    memcpy(grayscaleMsbBuffer, msbBuffer, BUFFER_SIZE);
    grayscaleMsbValid = true;
  }
}

void HalDisplay::copyGrayscaleLsbBuffers(const uint8_t *lsbBuffer) {
  if (!lsbBuffer) return;
  memcpy(grayscaleLsbBuffer, lsbBuffer, BUFFER_SIZE);
  grayscaleLsbValid = true;
}

void HalDisplay::copyGrayscaleMsbBuffers(const uint8_t *msbBuffer) {
  if (!msbBuffer) return;
  memcpy(grayscaleMsbBuffer, msbBuffer, BUFFER_SIZE);
  grayscaleMsbValid = true;
}

void HalDisplay::cleanupGrayscaleBuffers(const uint8_t *) {
  grayscaleLsbValid = false;
  grayscaleMsbValid = false;
}

void HalDisplay::displayGrayBuffer(bool, const unsigned char *, bool) {
  const uint8_t *bw = displayedBwValid ? displayedBwBuffer : getFrameBuffer();
  for (int y = 0; y < DISPLAY_HEIGHT; y++) {
    for (int x = 0; x < DISPLAY_WIDTH; x++) {
      const bool bwWhite = framebufferBitIsWhite(bw, x, y);
      const bool lsb = grayscaleLsbValid && framebufferBitIsWhite(grayscaleLsbBuffer, x, y);
      const bool msb = grayscaleMsbValid && framebufferBitIsWhite(grayscaleMsbBuffer, x, y);

      uint32_t color = 0xFFFFFFFF;
      if (!bwWhite) {
        if (msb && lsb) {
          color = 0xFF555555;
        } else if (msb) {
          color = 0xFFAAAAAA;
        } else {
          color = 0xFF000000;
        }
      }
      pixelBuf[y * DISPLAY_WIDTH + x] = color;
    }
  }
  pendingPresent.store(true);
}""",
        warn=False,
    )

    if changed:
        print(f"patch_simulator: patched grayscale display support for {pioenv}")

    strip_changed = False
    if hal_display_header.exists():
        header_text = hal_display_header.read_text()
        if "writeGrayscalePlaneStrip" not in header_text:
            header_marker = "  void cleanupGrayscaleBuffers(const uint8_t *bwBuffer);\n\n"
            header_insert = (
                "  void cleanupGrayscaleBuffers(const uint8_t *bwBuffer);\n"
                "  void writeGrayscalePlaneStrip(bool lsbPlane, const uint8_t *scratch, uint16_t yStart, "
                "uint16_t numRows);\n"
                "  bool supportsStripGrayscale() const;\n\n"
            )
            if header_marker in header_text:
                hal_display_header.write_text(header_text.replace(header_marker, header_insert, 1))
                strip_changed = True
            else:
                print(f"patch_simulator: strip grayscale header marker not found in {hal_display_header}")

    display_text = hal_display.read_text()
    if "HalDisplay::writeGrayscalePlaneStrip" not in display_text:
        display_marker = (
            "void HalDisplay::displayGrayBuffer(bool, const unsigned char *, bool) {\n"
        )
        display_insert = (
            "void HalDisplay::writeGrayscalePlaneStrip(bool, const uint8_t *, uint16_t, uint16_t) {}\n\n"
            "bool HalDisplay::supportsStripGrayscale() const { return false; }\n\n"
        )
        if display_marker in display_text:
            hal_display.write_text(display_text.replace(display_marker, display_insert + display_marker, 1))
            strip_changed = True
        else:
            print(f"patch_simulator: strip grayscale display marker not found in {hal_display}")

    if strip_changed:
        print(f"patch_simulator: patched strip grayscale compatibility for {pioenv}")

    http_changed = False
    if stream.exists():
        http_changed |= replace_once(
            stream,
            """  size_t readBytes(char *buffer, size_t length) { return 0; }""",
            """  virtual size_t readBytes(uint8_t *buffer, size_t length) {
    size_t count = 0;
    while (count < length && available() > 0) {
      const int c = read();
      if (c < 0) break;
      buffer[count++] = static_cast<uint8_t>(c);
    }
    return count;
  }
  virtual size_t readBytes(char *buffer, size_t length) {
    return readBytes(reinterpret_cast<uint8_t *>(buffer), length);
  }""",
            warn=False,
        )
        http_changed |= replace_once(
            stream,
            """  virtual size_t readBytes(char *buffer, size_t length) {
    size_t count = 0;
    while (count < length && available() > 0) {
      const int c = read();
      if (c < 0) break;
      buffer[count++] = static_cast<char>(c);
    }
    return count;
  }""",
            """  virtual size_t readBytes(uint8_t *buffer, size_t length) {
    size_t count = 0;
    while (count < length && available() > 0) {
      const int c = read();
      if (c < 0) break;
      buffer[count++] = static_cast<uint8_t>(c);
    }
    return count;
  }
  virtual size_t readBytes(char *buffer, size_t length) {
    return readBytes(reinterpret_cast<uint8_t *>(buffer), length);
  }""",
            warn=False,
        )

    if network_client.exists():
        http_changed |= replace_once(
            network_client,
            """class NetworkClientSecure : public NetworkClient {
public:
  void setInsecure() {}
};""",
            """class NetworkClientSecure : public NetworkClient {
public:
  void setInsecure() {}
  void setHandshakeTimeout(uint32_t) {}
};""",
        )

    if http_client.exists():
        http_changed |= replace_once(
            http_client,
            """enum { HTTPC_STRICT_FOLLOW_REDIRECTS, HTTP_CODE_OK = 200 };""",
            """enum {
  HTTPC_ERROR_CONNECTION_REFUSED = -1,
  HTTPC_ERROR_NOT_CONNECTED = -4,
  HTTPC_ERROR_CONNECTION_LOST = -5,
  HTTPC_ERROR_NO_HTTP_SERVER = -7,
  HTTPC_ERROR_READ_TIMEOUT = -11,
  HTTPC_STRICT_FOLLOW_REDIRECTS = 0,
  HTTP_CODE_OK = 200
};""",
        )
        http_changed |= replace_once(
            http_client,
            '''#include "SimHttpFetch.h"
#include "Stream.h"
#include "WString.h"''',
            '''#include "SimHttpFetch.h"
#include "Stream.h"
#include "StreamString.h"
#include "WString.h"''',
        )
        http_changed |= replace_once(
            http_client,
            """    responseBody_.s.clear();
    statusCode_ = 0;""",
            """    responseBody_.s.clear();
    responseStream_.clear();
    statusCode_ = 0;""",
        )
        http_changed |= replace_once(
            http_client,
            """  void setFollowRedirects(int mode) {}""",
            """  void setFollowRedirects(int mode) {}
  void setReuse(bool) {}
  void setConnectTimeout(int) {}
  void setTimeout(int) {}""",
        )
        http_changed |= replace_once(
            http_client,
            """  String getString() { return responseBody_; }
  int getSize() { return static_cast<int>(responseBody_.length()); }""",
            """  String getString() { return responseBody_; }
  int getSize() { return static_cast<int>(responseBody_.length()); }
  Stream *getStreamPtr() {
    responseStream_ = StreamString(responseBody_);
    return &responseStream_;
  }
  bool connected() const { return true; }
  static String errorToString(int) { return String("simulator HTTP error"); }""",
        )
        http_changed |= replace_once(
            http_client,
            """  String responseBody_;
  int statusCode_ = 0;""",
            """  String responseBody_;
  StreamString responseStream_;
  int statusCode_ = 0;""",
        )
        http_changed |= replace_once(
            http_client,
            """    responseBody_ = response.body;
    statusCode_ = response.statusCode;""",
            """    responseBody_ = response.body;
    responseStream_ = StreamString(responseBody_);
    statusCode_ = response.statusCode;""",
        )

    if http_changed:
        print(f"patch_simulator: patched HTTP client compatibility for {pioenv}")

    esp_http_changed = False
    if esp_http_client.exists():
        esp_http_changed |= replace_once(
            esp_http_client,
            """enum http_event { HTTP_EVENT_ON_DATA };""",
            """enum http_event { HTTP_EVENT_ON_DATA, HTTP_EVENT_ON_HEADER };""",
        )
        esp_http_changed |= replace_once(
            esp_http_client,
            """  void *data;
  int data_len;
  void *user_data;""",
            """  void *data;
  int data_len;
  const char *header_key;
  const char *header_value;
  void *user_data;""",
        )
        esp_http_changed |= replace_once(
            esp_http_client,
            """  int statusCode = 0;
  int contentLength = -1;""",
            """  int statusCode = 0;
  int contentLength = -1;
  std::string responseBody;
  size_t readOffset = 0;""",
        )
        esp_http_changed |= insert_before_once(
            esp_http_client,
            """inline esp_err_t esp_http_client_cleanup(esp_http_client_handle_t handle) {""",
            """inline esp_err_t esp_http_client_open(esp_http_client_handle_t handle, int) {
  if (!handle || !handle->config.url)
    return ESP_FAIL;

  using namespace sim_http_client_detail;

  const char *method = methodName(handle->config.method);
  sim_http_fetch::Response response;
  if (!sim_http_fetch::fetch(handle->config.url, method, handle->headers, "",
                             handle->postField.empty() ? nullptr : handle->postField.c_str(), response))
    return ESP_FAIL;

  handle->statusCode = response.statusCode;
  handle->responseBody = response.body;
  handle->contentLength = static_cast<int>(handle->responseBody.size());
  handle->readOffset = 0;
  return ESP_OK;
}

inline int64_t esp_http_client_fetch_headers(esp_http_client_handle_t handle) {
  if (!handle)
    return -1;
  return handle->contentLength;
}

inline int esp_http_client_read(esp_http_client_handle_t handle, char *buffer, int len) {
  if (!handle || !buffer || len <= 0)
    return ESP_FAIL;
  const size_t available = handle->responseBody.size() - handle->readOffset;
  const size_t count = available < static_cast<size_t>(len) ? available : static_cast<size_t>(len);
  if (count == 0)
    return 0;
  memcpy(buffer, handle->responseBody.data() + handle->readOffset, count);
  handle->readOffset += count;
  return static_cast<int>(count);
}

inline bool esp_http_client_is_complete_data_received(esp_http_client_handle_t handle) {
  return !handle || handle->readOffset >= handle->responseBody.size();
}

inline esp_err_t esp_http_client_get_and_clear_last_tls_error(esp_http_client_handle_t, int *esp_tls_error_code,
                                                             int *esp_tls_flags) {
  if (esp_tls_error_code)
    *esp_tls_error_code = 0;
  if (esp_tls_flags)
    *esp_tls_flags = 0;
  return ESP_OK;
}
""",
        )

    if esp_http_changed:
        print(f"patch_simulator: patched ESP HTTP client compatibility for {pioenv}")

    tilt_changed = False
    if hal_tilt_sensor.exists():
        tilt_changed |= replace_once(
            hal_tilt_sensor,
            """  void update(uint8_t /*mode*/, uint8_t /*orientation*/, bool /*inReader*/) {}""",
            """  void update(uint8_t /*mode*/, uint8_t /*direction*/, uint8_t /*orientation*/, bool /*inReader*/) {}""",
        )

    if tilt_changed:
        print(f"patch_simulator: patched tilt sensor compatibility for {pioenv}")


patch_simulator()
env.AddPreAction("buildprog", patch_simulator)
