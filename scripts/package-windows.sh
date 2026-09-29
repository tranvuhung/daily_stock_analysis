#!/usr/bin/env bash
#
# package-windows.sh — build Web UI trên Mac/Linux rồi đóng gói mã nguồn thành một
# file zip để chép sang máy Windows chạy dịch vụ qua mạng LAN.
# Build the Web UI on macOS/Linux and bundle the source into a zip for a Windows host.
#
# Gói gồm: mã nguồn theo `git archive HEAD` + static/ vừa build (static/ nằm trong
# .gitignore nên phải thêm riêng). KHÔNG gồm .venv, node_modules, .env, data/, logs/,
# reports/, docs/assets/ — virtualenv không dùng chéo nền tảng được, phải cài lại trên Windows bằng
# scripts/run-server.ps1 setup.
#
# Dùng: ./scripts/package-windows.sh [--skip-build] [--with-data] [--with-env] [--out DIR]
#
# Xem docs/windows-lan-deploy.md.
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

info() { printf '==> %s\n' "$*"; }
warn() { printf '[!] %s\n' "$*" >&2; }
die()  { printf '[x] %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<EOF
Dùng: $0 [tuỳ chọn]

  --skip-build   Không build lại frontend, dùng static/ hiện có
  --with-data    Kèm dữ liệu runtime trong data/ (DB SQLite được snapshot an toàn
                 bằng sqlite backup API, kèm file mật khẩu admin)
  --with-env     Kèm .env (CHỨA SECRET — chỉ dùng khi chép qua kênh tin cậy)
  --out DIR      Thư mục đầu ra (mặc định: dist/)
  -h, --help     Hiện trợ giúp
EOF
}

SKIP_BUILD=0
WITH_DATA=0
WITH_ENV=0
OUT_DIR="$REPO_ROOT/dist"

while (( $# )); do
  case "$1" in
    --skip-build) SKIP_BUILD=1 ;;
    --with-data)  WITH_DATA=1 ;;
    --with-env)   WITH_ENV=1 ;;
    --out)        shift; [[ $# -gt 0 ]] || die "--out cần một đường dẫn"; OUT_DIR="$1" ;;
    -h|--help)    usage; exit 0 ;;
    *)            usage >&2; die "Tuỳ chọn không hiểu: $1" ;;
  esac
  shift
done

command -v git >/dev/null 2>&1 || die "Cần git."
command -v zip >/dev/null 2>&1 || die "Cần lệnh zip."

# git archive chỉ lấy nội dung đã commit; cảnh báo để không vô tình bỏ sót thay đổi.
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  warn "Working tree có thay đổi chưa commit — gói chỉ chứa nội dung của HEAD (static/ thì lấy bản build hiện tại)."
fi

VERSION="$(git describe --tags --always --dirty 2>/dev/null || git rev-parse --short HEAD)"
PKG_NAME="dsa-windows-${VERSION}"

# --- 1. Build frontend --------------------------------------------------------
if (( SKIP_BUILD )); then
  info "Bỏ qua build frontend (--skip-build)"
else
  command -v npm >/dev/null 2>&1 || die "Cần Node.js/npm để build Web UI (hoặc dùng --skip-build)."
  info "Build Web UI (sản phẩm ghi vào static/)"
  # npm run lint không kiểm tra kiểu; lỗi kiểu chỉ lộ ra ở npm run build.
  ( cd apps/dsa-web && npm ci && npm run lint && npm run build )
fi

[[ -f static/index.html ]] || die "Thiếu static/index.html — build frontend thất bại hoặc chưa build."
[[ -f static/build-info.json ]] || warn "Thiếu static/build-info.json — không xác định được phiên bản frontend."

# --- 2. Dựng thư mục staging --------------------------------------------------
STAGE_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/dsa-pkg.XXXXXX")"
trap 'rm -rf "$STAGE_ROOT"' EXIT
STAGE="$STAGE_ROOT/$PKG_NAME"
mkdir -p "$STAGE"

info "Xuất mã nguồn từ HEAD ($(git rev-parse --short HEAD))"
# Bỏ ảnh/gif/psd của tài liệu (vài chục MB) — dịch vụ không dùng tới.
git archive --format=tar HEAD -- . ':(exclude)docs/assets' ':(exclude)sources' | tar -x -C "$STAGE"

info "Thêm static/ đã build"
rm -rf "$STAGE/static"
cp -R static "$STAGE/static"

mkdir -p "$STAGE/data" "$STAGE/logs" "$STAGE/reports"

if (( WITH_DATA )); then
  [[ -d data ]] || die "--with-data nhưng không có thư mục data/."
  info "Kèm dữ liệu runtime từ data/"
  # Chép file cấu hình/secret nhỏ nằm trong data/ (ví dụ file hash mật khẩu admin),
  # bỏ qua DB (snapshot riêng bên dưới), file lock, cache và các bản backup cũ.
  find data -maxdepth 1 -type f \
    ! -name '*.db' ! -name '*.db-wal' ! -name '*.db-shm' ! -name '*.db-journal' \
    ! -name '*.lock' ! -name '*.pid' \
    -exec cp -p {} "$STAGE/data/" \;
  DB_PATH="data/stock_analysis.db"
  if [[ -f "$DB_PATH" ]]; then
    # DB chạy ở chế độ WAL: chép file .db trần sẽ mất dữ liệu còn nằm trong -wal.
    # Dùng sqlite backup API để có một snapshot nhất quán.
    PY="python3"
    [[ -x .venv/bin/python ]] && PY=".venv/bin/python"
    "$PY" - "$DB_PATH" "$STAGE/$DB_PATH" <<'PY'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1])
dst = sqlite3.connect(sys.argv[2])
with dst:
    src.backup(dst)
dst.execute("PRAGMA journal_mode=DELETE")
dst.close()
src.close()
PY
    info "Snapshot DB: $DB_PATH ($(du -h "$STAGE/$DB_PATH" | cut -f1))"
  else
    warn "Không thấy $DB_PATH — bỏ qua DB."
  fi
fi

if (( WITH_ENV )); then
  [[ -f .env ]] || die "--with-env nhưng không có .env."
  warn "Kèm .env vào gói — file chứa API key/mật khẩu. Đừng chia sẻ file zip này."
  cp .env "$STAGE/.env"
fi

# --- 3. Nén -------------------------------------------------------------------
mkdir -p "$OUT_DIR"
ZIP_PATH="$(cd "$OUT_DIR" && pwd)/${PKG_NAME}.zip"
rm -f "$ZIP_PATH"
info "Nén: $ZIP_PATH"
( cd "$STAGE_ROOT" && zip -qr "$ZIP_PATH" "$PKG_NAME" )
(( WITH_ENV )) && chmod 600 "$ZIP_PATH"

SHA="$(shasum -a 256 "$ZIP_PATH" | cut -d' ' -f1)"

cat <<EOF

    Gói    : $ZIP_PATH
    Cỡ     : $(du -h "$ZIP_PATH" | cut -f1)
    SHA256 : $SHA
    Kèm DB : $( (( WITH_DATA )) && echo có || echo không )
    Kèm .env: $( (( WITH_ENV )) && echo "CÓ (chứa secret)" || echo không )

Bước tiếp theo trên máy Windows (PowerShell, trong thư mục đã giải nén):
  Get-FileHash .\\${PKG_NAME}.zip -Algorithm SHA256   # so với SHA256 ở trên
  .\\scripts\\run-server.ps1 setup                     # tạo .venv + cài dependency
  notepad .env                                        # WEBUI_HOST=0.0.0.0, ADMIN_AUTH_ENABLED=true, ...
  .\\scripts\\run-server.ps1 firewall                  # PowerShell quyền Administrator
  .\\scripts\\run-server.ps1 start

Chi tiết: docs/windows-lan-deploy.md
EOF
