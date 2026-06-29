# config.py
APP_NAME = "動画名人"
APP_VERSION = "1.0.0"
APP_INTERNAL_NAME = "DougaMeijin"

WARNING_TEXT = (
    "著作権に関する注意！！\n\n"
    "1. 著作権法の遵守:\n"
    "著作物を利用する場合は、適切な引用または許諾が必要です。"
    "引用の範囲をこえる利用などの不適切な著作物（スキャンしたページ、画像、音声など）利用は法律で禁じられています。\n\n"
    "2. 法律で認められている利用:\n"
    "・私的利用: あなた自身やご家庭内など、限られた範囲内で利用するために著作物を複製することは認められています。\n"
    "・教育機関での利用: 学校や大学などでは、授業の過程で必要な場合に限り、著作物の複製や公衆送信（学生・生徒へのオンライン配信など）が例外的に認められています。「授業目的公衆送信補償金制度」についてもご確認ください。\n\n"
    "3. あなたの責任:\n"
    "・【重要】公開・譲渡の禁止: 作成した動画をインターネット上（SNS、動画サイトなど）で公開したり、友人・知人に譲渡したりする行為は、著作権侵害として法律に触れる恐れがあります。\n"
    "・最終責任: ご自身の利用方法が法律に準拠しているかを確認する最終的な責任は、ユーザーに存します。\n\n"
    "なお、これは法的助言ではなく、必要な注意喚起として表示しています。"
)

DISCLAIMER_TEXT = (
    "・本アプリは現状のまま（“AS IS”）提供され、商品性、特定目的適合性、権利非侵害を含む"
    "いかなる明示・黙示の保証も行いません。\n"
    "・本アプリの使用は利用者自身の責任（at your own risk）で行ってください。\n"
    "・開発者・著作権者は、本アプリの使用または使用不能から生じるいかなる損害"
    "（データ消失、機器故障、業務中断、逸失利益、間接・付随的・特別・懲罰的損害を含む）に対しても一切責任を負いません。"
    "重要データは事前にバックアップしてください。\n"
    "・本アプリは FFmpeg 等の第三者コンポーネントを利用します。各コンポーネントのライセンスと条件に従ってください"
    "（メニューの「ヘルプ → 謝辞」で参照可能）。\n"
    "・本アプリで生成・書き出し・公開した出力物の内容や配布の適法性・適切性の最終責任は利用者にあります。\n"
)

# --- External Dependencies & Project Structure ---
HOMEPAGE_URL = "https://yosukey.github.io/DougaMeijin"
GITHUB_REPO_ID = "yosukey/DougaMeijin"
FFMPEG_API_URL = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest"
FFMPEG_TARGET_ZIP_FILENAME = "ffmpeg-master-latest-win64-gpl.zip"
FFMPEG_GLOBAL_CHECKSUM_FILENAME = "checksums.sha256"

FFMPEG_DOWNLOADER_USER_AGENT = f"{APP_INTERNAL_NAME}-Downloader ({GITHUB_REPO_ID})"

PROJECT_FILE_EXTENSION = ".dmj"
PROJECT_FILENAME = "project.json"

DIR_IMAGES = "images"
DIR_AUDIO = "audio"
DIR_THUMBNAILS = "_thumbnails"
DIR_WAVEFORMS = "_waveforms"

# Y2 comment: Hardcoded .exe names. Assumes Windows (win64-gpl builds).
FFMPEG_INSTALL_DIR = "ffmpeg"
FFMPEG_BIN_DIR = "bin"
FFMPEG_EXE = "ffmpeg.exe"
FFPROBE_EXE = "ffprobe.exe"


# --- Application Behavior Settings ---
PDF_RENDER_DPI = 200
MASTER_IMAGE_FORMAT_NAME = "PNG"
MASTER_IMAGE_EXTENSION = ".png"
COLLISION_RETRY_LIMIT = 200
FFMPEG_AUDIO_FILTER = "dynaudnorm=f=150:g=15:p=0.9"
RECORDER_PREFERRED_RATES = [24000, 48000, 44100, 16000]

# --- Audio Persistence Format ---
# Persisted (in-project) audio is stored as lossless FLAC.
# Readers tolerate legacy WAV; loading migrates WAV -> FLAC.
AUDIO_FILE_EXTENSION = ".flac"
AUDIO_CODEC = "flac"
# The recorder writes a transient WAV; the worker encodes it to FLAC.
RECORDING_TEMP_SUFFIX = ".recording.wav"


# --- UI Default Strings ---
DEFAULT_PROJECT_NAME = "無題のプロジェクト"
PROJECT_FILTER_NAME = "動画名人 プロジェクト"
IMAGE_PDF_FILE_FILTER = "画像とPDFファイル (*.png *.jpg *.jpeg *.bmp *.webp *.pdf);;すべてのファイル (*)"
DEFAULT_EXPORT_FILENAME = "output.mp4"


# --- UI Layout ---
THUMBNAIL_WIDTH = 160
THUMBNAIL_HEIGHT = 90
LIST_WIDGET_FONT_SIZE = 16

# --- Default Project Settings ---
DEFAULT_RESOLUTION = "720p"
DEFAULT_FPS = 15

# --- Video Export ---
RESOLUTION_MAP = {
    "1080p": (1920, 1080),
    "720p": (1280, 720),
}
MIN_EXPORT_FPS = 10
H264_PRESET = "veryfast"
H264_CRF = "26"
GOP_MULTIPLIER = 10

# --- Audio Settings ---
# Y2 comment: Target audio spec (16kHz, Mono, 64k AAC) is an intentional design choice.
# Optimizes heavily for narration clarity and small file size, NOT music fidelity.
AUDIO_BITRATE = "64k"
AUDIO_RATE = "16000"
AUDIO_CHANNELS = "1"
MIN_AUDIO_DURATION_SEC = 0.1
AUDIO_TRIM_END_DURATION_SEC = 0.2

# --- Recording Settings ---
MIN_RECORDING_DURATION_SEC = 3.0

# --- Transitions ---
TRANSITIONS = {
    "none": "なし",
    "fade": "フェード",
    "wipeleft": "右からスライド (横書き用)",
    "wiperight": "左からスライド(縦書き用)",
    "circleopen": "円形"
}
TRANSITION_TOTAL_SECONDS = 2.0

# --- UI and Behavior Settings (Moved from Magic Numbers) ---
INITIAL_WINDOW_WIDTH = 1280
INITIAL_WINDOW_HEIGHT = 720

PREVIEW_MIN_WIDTH = 640
PREVIEW_MIN_HEIGHT = 360

WAVEFORM_WIDGET_HEIGHT = 100
WAVEFORM_TIMELINE_HEIGHT = 15

PAGE_LIST_ITEM_PADDING = 10

STATUS_BAR_MSG_DURATION_MS = 2000
STATUS_BAR_SAVE_MSG_DURATION_MS = 3000

NO_PLAYBACK_POSITION = -1.0

# --- UI Colors ---
# Main Colors
COLOR_WHITE = "#ffffff"

# Button Base Colors
COLOR_RED_BASE = "#d32f2f"
COLOR_RED_HOVER = "#e53935"
COLOR_RED_PRESSED = "#c62828"

COLOR_BLUE_BASE = "#1976d2"
COLOR_BLUE_HOVER = "#1e88e5"
COLOR_BLUE_PRESSED = "#1565c0"

COLOR_GREEN_BASE = "#388e3c"
COLOR_GREEN_HOVER = "#43a047"
COLOR_GREEN_PRESSED = "#2e7d32"

# Button Disabled Colors
COLOR_DISABLED_BG = "#f5f5f5"
COLOR_DISABLED_TEXT = "#bbbbbb"
COLOR_DISABLED_BORDER = "#dddddd"

# Level Monitor Bar Colors
LEVEL_BAR_GREEN = "#4CAF50"
LEVEL_BAR_YELLOW = "#FFC107"
LEVEL_BAR_RED = "#F44336"

# --- File Import Limits ---
MAX_FILES_TO_ADD_AT_ONCE = 100
MAX_IMAGE_FILE_SIZE_MB = 50
MAX_PDF_FILE_SIZE_MB = 120
MAX_PDF_PAGE_COUNT = 100
MAX_IMAGE_PIXELS = 35 * 1000 * 1000  # 35 megapixels
MAX_UNCOMPRESSED_PROJECT_SIZE_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB