# PaperRead CLI macOS 版

版本：1.4.7-cli.7。這是以 [YuriyLin/PaperRead v1.4.7](https://github.com/YuriyLin/PaperRead/releases/tag/v1.4.7) 修改的非官方社群版本；原作者與貢獻者的 MIT 授權、版權及第三方套件聲明均保留。

## 選擇安裝檔

需求：macOS 14 Sonoma 或更新版本。建置與自動測試使用 macOS 15。

| 你的 Mac | 下載檔案 |
|---|---|
| Apple Silicon（M1、M2、M3、M4 等 M 系列晶片） | `PaperRead-CLI-1.4.7-cli.7-macOS-arm64.dmg` |
| Intel 處理器 | `PaperRead-CLI-1.4.7-cli.7-macOS-x64.dmg` |

可在蘋果選單 →「關於這台 Mac」確認晶片。兩個檔案各自包含對應架構的 Electron 與 Python 後端；Apple Silicon 版不需要 Rosetta，也不必自行安裝 Python 或 Node.js。

Windows 使用者請使用 [Windows 安裝版](https://github.com/jimmymochi/paperread-cli/releases/tag/cli-v1.4.7-7)。

## 安裝與首次開啟

1. 下載適合晶片的 DMG，打開後把 **PaperRead CLI** 拖進 **Applications／應用程式**。
2. 從「應用程式」開啟 PaperRead CLI。不要一直從 DMG 內執行。
3. 此版只有 ad-hoc 簽署，尚未取得 Apple Developer ID 簽署及公證。若 macOS 因無法驗證開發者而阻擋，先確認來源與隨附 SHA256，再到「系統設定 → 隱私權與安全性」查看「強制打開」選項。不同系統或學校管理政策可能不允許開啟。

可用終端機核對下載檔，例如：

```bash
shasum -a 256 ~/Downloads/PaperRead-CLI-1.4.7-cli.7-macOS-arm64.dmg
```

比對 Release 內 `SHA256SUMS-macOS.txt` 的對應項目。Intel 請改用 x64 檔名。

## 使用自己的 AGY／Codex 登入

安裝包不含 AGY 或 Codex CLI，也不含其他人的授權。依自己要用的來源安裝並登入：

- [Antigravity CLI 官方說明](https://www.agy.dev/docs/cli/)：在終端機執行 `agy` 完成登入。
- [Codex CLI 官方說明](https://developers.openai.com/codex/cli/)：在終端機執行 `codex` 完成登入。

登入後重新開啟 PaperRead CLI，在「設定 → 模型」選擇來源。AGY 可按「重新整理 AGY 模型」，使用自己的模型清單。日常翻譯及問 AI 在背景呼叫已登入的 CLI，PaperRead 不要求填 API Key。

從 Finder 開啟時會載入登入 shell 的 PATH，並檢查 Homebrew、`/usr/local/bin`、`~/.local/bin` 等常見路徑。若仍找不到 CLI，可在終端機執行 `command -v agy` 或 `command -v codex`，把完整路徑填入對應的自訂 CLI 路徑。

首頁右下角可切換 Codex／AGY 的 5hr、Week 額度。抓住卡片空白、邊緣、數字或標題可拖曳；「−」收起、「＋」展開。CLI 沒有回傳的額度維持「—」。

## 資料與更新

- 文獻與設定：`~/Library/Application Support/PaperReadCLI/data`
- 視窗偏好：`~/Library/Application Support/PaperReadCLI`
- 更新：先結束程式，再以新 DMG 的程式取代「應用程式」中的舊版；使用者資料位於上述獨立目錄。
- 關閉視窗後可從 Dock 重開；使用 `⌘Q` 完整結束程式。
- 全螢幕：按 `Control＋Command＋F` 進出，或使用綠色視窗按鈕；按 `Esc` 可離開。cli.7 也修正文獻庫外側多餘捲軸與深色主題的原生捲軸顏色。

安裝包與原始碼不含製作者的論文、帳號授權、API Key 或個人設定。

## 建置與驗證範圍

`.github/workflows/macos.yml` 在 Apple Silicon 與 Intel 的 GitHub macOS runner 各自建置。兩邊都必須通過 JavaScript、相關 Python 測試、DMG 掛載、應用程式複製、架構及簽章檢查、打包後端 PDF 匯入與圖片縮放，以及 Electron 啟動／首頁載入，才會發布。

測試使用空白文獻庫，不使用真實 AGY／Codex 帳號，也不呼叫 AI 生成回答。這些自動測試不包含從瀏覽器下載後的 Gatekeeper 提示或所有 macOS 版本的人工操作測試。

Intel 使用仍提供對應安裝包的 ONNX Runtime 1.23.2 與 cryptography 48.0.1；Apple Silicon 分別使用 1.31.0 與 50.0.2。其餘建置版本記錄於 `requirements-build.txt` 與 `package-lock.json`。CI 只接受預先編譯的套件，避免在不同機器臨時編譯而改變最低系統需求。

本機建置必須在相同晶片架構的 Mac 上執行，需 Python 3.13、Node.js 22、Xcode Command Line Tools：

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-build.txt
python -m pip install --no-deps -e .
npm ci
npm run dist:mac
```

DMG 產出於 `dist/electron/`。不可把 Windows 的後端 EXE 放進 Mac 安裝包，也不可直接把兩個 PyInstaller 單檔執行檔合併成 universal binary。
