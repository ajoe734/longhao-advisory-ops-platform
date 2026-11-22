# HerdR 操作與交付手冊

## 定位與查證

HerdR 管理 agent 所在的終端與 workspace；實際寫程式的是各 pane 內的 coding agent。本專案使用十四 worktree 隔離十四模組。依官方文件查證於 2026-11-22；命令若與本機版本不同，以 `herdr --help`、子命令 help 及本機 API schema 為準，不套用虛構的 `herdr.yaml` 格式。

官方來源：[Agent guide](https://herdr.dev/agent-guide.md)、[CLI reference](https://herdr.dev/tui/docs/cli-reference/)、[Socket API](https://herdr.dev/tui/docs/socket-api/)。

## 啟動之前

在使用者開發機準備 Git、Python 3.12、Docker/Compose（跑 PostgreSQL16 測試）、coding agent 與已授權的模型。HerdR 的安裝依官方安裝頁。不要把模型 key 或 GitHub token 寫進本倉庫。

```bash
git clone https://github.com/ajoe734/longhao-advisory-ops-platform.git
cd longhao-advisory-ops-platform
python -m pip install -r requirements-dev.txt
python scripts/validate_docs.py
python scripts/validate_contracts.py
python scripts/prepare_worktrees.py --base main --apply
herdr --version
```

從尚未位於 HerdR pane 的終端執行 `herdr`；已在 HerdR 內則沿用現有 session。官方 guide 提醒不要從 pane 再啟一層互動式 HerdR。

## 同時分派十四模組

`project/module-manifest.json` 描述十四個工作流，並非 HerdR 原生配置。`prepare_worktrees.py` 只使用 Git 建立隔離分支，不代替 agent 寫程式。

在協調者 pane 啟動 coding agent，貼入 `agents/COORDINATOR_PROMPT.md`。讓協調者依本機 CLI/API schema 建立十四個 workspace/pane、各自切入 worktree、啟動模組 agent，再送出對應 task packet。可以使用官方 CLI 的 workspace create、agent start、agent prompt、agent list；不得捏造 pane ID，也不得把建立 pane 的成功回應當作模組實作成功。

協調者應同時送出全部十四份工作，不等待 M01 完成才送 M02。模型配額或記憶體不足時顯示實際並行數，保留全部工作範圍並調整執行環境。

## 每個 agent 的第一則任務

讀根目錄 AGENTS.md、自己 task packet、相應模組規格、API/事件/資料契約。回報所屬 issue、branch、owned paths、使用哪個 mock、最先要通過的業務案例，然後開始實作。產出後端、管理介面、worker、測試與操作說明，不能只交 another plan。

## 整合者作業

- 每個 PR 先驗 shared contract 再驗 module；合併有序，開發平行。
- 共用 migration、auth、UI shell、dependencies 由 steward 收斂，不能讓十四個分支互相覆寫。
- 需要其他模組的功能時，以對應 fake 繼續做自己的 behavior；同時提交 provider contract test 到 integration queue。
- 累積實作要在 staging 同步跑全鏈路。任務未通過完整 acceptance，不以 agent 的 done 標籤結案。
- `project/completion-template.md` 填實際證據，Issue 只在實作與整合通過後關閉。

## 中斷與接續

在每個 PR/任務記錄目前 head、測試、未完成案例與 provider 狀態。重新連線後先核對 live agent list、工作樹與 branch，不憑上次 sidebar 狀態重複啟動 agent。HerdR 的 pane 存活與機器重啟後 agent 恢復取決於環境與整合，不能保證原程序跨重啟持續運作。

## 本輪交付狀態

此文件提供完整分派方法與程式驗證材料；本次未連線到使用者的 HerdR，也未假稱十四個程式開發 agent 已在其主機開始執行。下一步由協調者按同一基線一次分派十四任務，最終以全範圍 G3 驗收完成為準。
