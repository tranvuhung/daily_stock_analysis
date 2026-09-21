#!/usr/bin/env bash
#
# run-app.sh — khởi động và vận hành daily_stock_analysis trên máy cục bộ.
#
# Đóng gói quy trình trong .claude/skills/run-app/SKILL.md thành lệnh chạy được.
# Không đụng vào .env của repo: mọi override được ghi ra một file env tạm rồi
# trỏ ENV_FILE vào đó (STOCK_LIST / REPORT_LANGUAGE chỉ có hiệu lực khi nằm
# trong file env, không đọc từ biến môi trường của tiến trình).
#
# Dùng: ./scripts/run-app.sh [serve|dry-run|analyze|smoke|stop|build-web] [tuỳ chọn]
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# --- Cấu hình: mọi giá trị đều override được qua biến môi trường ---------------
_RUN_TMP_BASE="${TMPDIR:-/tmp}"
RUN_DIR="${RUN_DIR:-${_RUN_TMP_BASE%/}/dsa-run}"
RUN_DIR="${RUN_DIR%/}"
RUN_PORT="${RUN_PORT:-8765}"
RUN_HOST="${RUN_HOST:-127.0.0.1}"
RUN_MARKETS="${RUN_MARKETS:-vn}"
RUN_STOCK_LIST="${RUN_STOCK_LIST:-HPG,FPT,VNM,VNINDEX,VN30}"
RUN_REPORT_LANGUAGE="${RUN_REPORT_LANGUAGE:-vi}"
RUN_MARKET_REVIEW="${RUN_MARKET_REVIEW:-false}"
RUN_WEBUI_AUTO_BUILD="${RUN_WEBUI_AUTO_BUILD:-false}"
RUN_HEALTH_TIMEOUT="${RUN_HEALTH_TIMEOUT:-60}"

ENV_TMP="$RUN_DIR/run.env"
DB_TMP="$RUN_DIR/run.db"
LOG_FILE="$RUN_DIR/server.log"
PID_FILE="$RUN_DIR/server.pid"
BASE_URL="http://${RUN_HOST}:${RUN_PORT}"

# Các khoá bị gỡ khỏi bản sao .env rồi ghi đè bằng giá trị của script.
OVERRIDE_KEYS='SUPPORTED_MARKETS|STOCK_LIST|REPORT_LANGUAGE|DATABASE_PATH|WEBUI_ENABLED|WEBUI_PORT|WEBUI_AUTO_BUILD|MARKET_REVIEW_ENABLED'

info() { printf '==> %s\n' "$*"; }
warn() { printf '[!] %s\n' "$*" >&2; }
die()  { printf '[x] %s\n' "$*" >&2; exit 1; }

# --- Môi trường Python --------------------------------------------------------
ensure_python() {
  if [[ -z "${VIRTUAL_ENV:-}" && -f .venv/bin/activate ]]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
    info "Đã kích hoạt .venv"
  fi
  command -v python >/dev/null 2>&1 || die "Không tìm thấy python. Tạo venv: python3.11 -m venv .venv && pip install -r requirements.txt"
  local ver
  ver="$(python -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
  [[ "$ver" == "3.11" ]] || warn "Python $ver (repo dùng 3.11) — có thể gặp sai khác về dependency."
}

# --- Sinh file env tạm --------------------------------------------------------
# $1: giá trị WEBUI_ENABLED (true khi chạy dịch vụ, false khi chạy một lượt).
write_env_file() {
  local webui_enabled="$1"
  [[ -f .env ]] || die "Thiếu .env ở thư mục gốc repo. Sao chép từ .env.example rồi điền key trước."
  mkdir -p "$RUN_DIR"
  # Tạo file rỗng với quyền 600 TRƯỚC khi chép nội dung: .env chứa secret thật.
  : > "$ENV_TMP"
  chmod 600 "$ENV_TMP"
  { grep -vE "^[[:space:]]*(${OVERRIDE_KEYS})=" .env || true; } >> "$ENV_TMP"
  cat >> "$ENV_TMP" <<EOF

# --- ghi đè bởi scripts/run-app.sh ---
SUPPORTED_MARKETS=${RUN_MARKETS}
STOCK_LIST=${RUN_STOCK_LIST}
REPORT_LANGUAGE=${RUN_REPORT_LANGUAGE}
DATABASE_PATH=${DB_TMP}
WEBUI_ENABLED=${webui_enabled}
WEBUI_PORT=${RUN_PORT}
WEBUI_AUTO_BUILD=${RUN_WEBUI_AUTO_BUILD}
MARKET_REVIEW_ENABLED=${RUN_MARKET_REVIEW}
EOF
  info "Cấu hình chạy: $ENV_TMP (thị trường=$RUN_MARKETS, ngôn ngữ=$RUN_REPORT_LANGUAGE, DB=$DB_TMP)"
}

warn_if_no_llm_key() {
  if ! grep -qE '^[[:space:]]*(GEMINI|OPENAI|ANTHROPIC|DEEPSEEK|QWEN|MOONSHOT|ZHIPU)_API_KEY=.+' "$ENV_TMP"; then
    warn "Không thấy LLM API key trong .env — Web UI sẽ báo 'Base configuration incomplete'; chế độ dry-run vẫn chạy."
  fi
}

# --- Quản lý cổng / tiến trình ------------------------------------------------
port_pids() { lsof -ti:"$RUN_PORT" -sTCP:LISTEN 2>/dev/null || true; }

free_port() {
  local pids
  pids="$(port_pids)"
  [[ -z "$pids" ]] && return 0
  info "Cổng $RUN_PORT đang bị chiếm (PID: $(echo "$pids" | tr '\n' ' ')) — dừng tiến trình cũ"
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null || true
  sleep 2
  pids="$(port_pids)"
  if [[ -n "$pids" ]]; then
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
    sleep 1
  fi
  [[ -z "$(port_pids)" ]] || die "Không giải phóng được cổng $RUN_PORT."
}

wait_for_health() {
  local i
  for ((i = 1; i <= RUN_HEALTH_TIMEOUT; i++)); do
    if curl -sf "${BASE_URL}/api/v1/health" >/dev/null 2>&1; then
      info "Dịch vụ sẵn sàng sau ${i}s — ${BASE_URL}"
      return 0
    fi
    # Tiến trình chết sớm thì đừng chờ hết timeout.
    if [[ -f "$PID_FILE" ]] && ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      break
    fi
    sleep 1
  done
  warn "Dịch vụ không phản hồi /api/v1/health. 40 dòng cuối của log:"
  tail -40 "$LOG_FILE" >&2 || true
  return 1
}

# --- Smoke test ---------------------------------------------------------------
run_smoke() {
  local sample_stock sample_index disabled_code code
  sample_stock="${RUN_SMOKE_STOCK:-HPG}"
  sample_index="${RUN_SMOKE_INDEX:-VNINDEX}"
  disabled_code="${RUN_SMOKE_DISABLED_CODE:-600519}"

  info "Tổng quan nguồn dữ liệu"
  curl -s "${BASE_URL}/api/v1/data/overview" | python -m json.tool | head -40

  info "Cổ phiếu Việt Nam: $sample_stock"
  curl -s "${BASE_URL}/api/v1/stocks/${sample_stock}/quote" | head -c 600; echo

  info "Chỉ số Việt Nam: $sample_index"
  curl -s "${BASE_URL}/api/v1/stocks/${sample_index}/quote" | head -c 600; echo

  code="$(curl -s -o /dev/null -w '%{http_code}' "${BASE_URL}/api/v1/stocks/${disabled_code}/quote")"
  if [[ "$code" == "404" ]]; then
    info "Mã thuộc thị trường đã tắt ($disabled_code) -> HTTP $code (đúng kỳ vọng)"
  else
    warn "Mã thuộc thị trường đã tắt ($disabled_code) -> HTTP $code (kỳ vọng 404) — cổng chặn thị trường có thể đã hỏng."
  fi
}

# Kiểm tra báo cáo không lẫn ký tự CJK. Dùng python vì BSD grep trên macOS
# không có -P.
check_cjk_leak() {
  local target="$1"
  [[ -e "$target" ]] || { warn "Không tìm thấy báo cáo để kiểm tra: $target"; return 0; }
  python - "$target" <<'PY'
import re, sys, pathlib
pattern = re.compile(r'[一-鿿぀-ヿ가-힯]')
paths = [pathlib.Path(sys.argv[1])]
if paths[0].is_dir():
    paths = sorted(paths[0].glob('report_*.md'))
leaked = False
for path in paths:
    for lineno, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        if pattern.search(line):
            leaked = True
            print(f'{path}:{lineno}: {line.strip()[:160]}')
if leaked:
    print('\n[!] Báo cáo còn ký tự CJK. Văn bản tự do do LLM sinh thì chấp nhận được;')
    print('    nhưng nhãn, văn bản cố định và giá trị enum ra tiếng Trung là bug code.')
else:
    print('no CJK leak')
PY
}

# --- Các lệnh -----------------------------------------------------------------
cmd_serve() {
  local background=0
  [[ "${1:-}" == "--background" ]] && background=1

  ensure_python
  write_env_file true
  warn_if_no_llm_key
  free_port

  info "Khởi động dịch vụ (log: $LOG_FILE)"
  ENV_FILE="$ENV_TMP" nohup python main.py --serve-only > "$LOG_FILE" 2>&1 &
  echo $! > "$PID_FILE"

  if ! wait_for_health; then
    rm -f "$PID_FILE"
    die "Khởi động thất bại."
  fi

  run_smoke

  cat <<EOF

    Web UI : ${BASE_URL}/
    API doc: ${BASE_URL}/docs
    Log    : ${LOG_FILE}

Sau khi sửa frontend, thêm tham số truy vấn (ví dụ ${BASE_URL}/?v=2) để né cache bundle cũ,
và mở console trình duyệt một lượt — lỗi frontend chỉ in ra console, trên trang chỉ biểu hiện
thành "không có gợi ý xổ xuống".
EOF

  if (( background )); then
    info "Chạy nền, PID $(cat "$PID_FILE"). Dừng bằng: $0 stop"
    return 0
  fi

  info "Nhấn Ctrl+C để dừng."
  trap 'echo; info "Đang dừng..."; cmd_stop; exit 0' INT TERM
  wait "$(cat "$PID_FILE")" || true
}

cmd_dry_run() {
  ensure_python
  write_env_file false
  info "Chỉ lấy dữ liệu, không gọi LLM"
  ENV_FILE="$ENV_TMP" python main.py --dry-run
}

cmd_analyze() {
  local stocks="${1:-${RUN_STOCK_LIST%%,*}}"
  ensure_python
  # WEBUI_ENABLED phải là false, nếu không lệnh sẽ treo chạy dịch vụ sau khi phân tích xong.
  write_env_file false
  warn_if_no_llm_key
  info "Phân tích thật: $stocks (--no-notify: không đẩy thông báo ra kênh nào)"
  ENV_FILE="$ENV_TMP" python main.py --stocks "$stocks" --no-notify
  info "Kiểm tra lẫn ngôn ngữ trong báo cáo"
  check_cjk_leak "${RUN_REPORT_DIR:-reports}"
}

cmd_smoke() {
  curl -sf "${BASE_URL}/api/v1/health" >/dev/null 2>&1 \
    || die "Dịch vụ chưa chạy ở ${BASE_URL}. Khởi động trước: $0 serve --background"
  run_smoke
}

cmd_stop() {
  local pids
  pids="$(port_pids)"
  if [[ -z "$pids" ]]; then
    info "Không có tiến trình nào đang nghe cổng $RUN_PORT."
  else
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null || true
    sleep 1
    pids="$(port_pids)"
    # shellcheck disable=SC2086
    [[ -n "$pids" ]] && kill -9 $pids 2>/dev/null || true
    info "Đã dừng dịch vụ trên cổng $RUN_PORT."
  fi
  rm -f "$PID_FILE"
}

cmd_build_web() {
  info "Build frontend (sản phẩm ghi vào $REPO_ROOT/static/)"
  # npm run lint không kiểm tra kiểu; lỗi kiểu chỉ lộ ra ở npm run build.
  ( cd apps/dsa-web && npm run lint && npm run build )
}

usage() {
  cat <<EOF
Dùng: $0 <lệnh> [tuỳ chọn]

Lệnh:
  serve [--background]   Khởi động FastAPI + Web UI, chờ health, chạy smoke test (mặc định)
  dry-run                Chỉ lấy dữ liệu, không gọi LLM — cách smoke test nhanh nhất
  analyze [MÃ]           Chạy một lượt phân tích thật (cần LLM key), rồi kiểm tra lẫn ngôn ngữ
  smoke                  Chạy smoke test trên dịch vụ đang chạy sẵn
  stop                   Dừng dịch vụ đang nghe cổng $RUN_PORT
  build-web              npm run lint && npm run build trong apps/dsa-web

Biến môi trường (đều có mặc định):
  RUN_DIR=$RUN_DIR
  RUN_PORT=$RUN_PORT                RUN_HOST=$RUN_HOST
  RUN_MARKETS=$RUN_MARKETS
  RUN_STOCK_LIST=$RUN_STOCK_LIST
  RUN_REPORT_LANGUAGE=$RUN_REPORT_LANGUAGE
  RUN_MARKET_REVIEW=$RUN_MARKET_REVIEW      RUN_WEBUI_AUTO_BUILD=$RUN_WEBUI_AUTO_BUILD
  RUN_HEALTH_TIMEOUT=$RUN_HEALTH_TIMEOUT

Ví dụ:
  $0 serve                          # khởi động, giữ ở foreground
  RUN_PORT=9000 $0 serve            # đổi cổng
  RUN_MARKETS=vn,cn $0 dry-run      # bật lại thị trường khác để rollback thử
  $0 analyze FPT                    # phân tích thật một mã
EOF
}

case "${1:-serve}" in
  serve)     shift || true; cmd_serve "$@" ;;
  dry-run)   cmd_dry_run ;;
  analyze)   shift || true; cmd_analyze "$@" ;;
  smoke)     cmd_smoke ;;
  stop)      cmd_stop ;;
  build-web) cmd_build_web ;;
  -h|--help|help) usage ;;
  *)         usage >&2; exit 2 ;;
esac
