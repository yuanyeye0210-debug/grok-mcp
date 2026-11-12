# Grok MCP 服务器

让 Codex、Antigravity（还有其他 MCP 客户端）直接问 Grok 的小工具。
Agent HUD 的 Grok 只能看用量、不能发消息，这个工具绕过 HUD：
Grok 变成一个 MCP 工具，其他 agent 在自己的回合里调它拿答案。

- 一个文件，Python 标准库，没有任何 pip 依赖
- 调 xAI 的 Responses API（`https://api.x.ai/v1/responses`，模型默认 `grok-4.7`）
- 密钥只从环境变量 `XAI_API_KEY` 读，不写进任何文件、不打印、不记录

## 文件

| 文件 | 说明 |
| --- | --- |
| `grok_mcp_server.py` | 服务器本身（stdio MCP） |
| `test_grok_mcp_server.py` | 测试，17 个，HTTP 全是 mock，不用 key、不用联网 |
| `config-codex.toml` | Codex 的配置片段 |
| `config-antigravity.json` | Antigravity 的配置片段 |
| `README.md` | 本文件 |

## 1. 拿 xAI API key（5 分钟）

1. 去 https://console.x.ai 注册/登录
2. Billing 里先充一点预付费（API 没有免费额度，订阅的钱也不通用）
3. API Keys 页面建一个 key（`xai-` 开头）
4. 记下价格：grok-4.7 约 $2/$6 每百万 token（输入/输出），问一次日常问题通常几分钱

## 2. 放到 Windows 电脑上

1. 把 `grok_mcp_server.py` 放到一个以后不动的文件夹，比如 `C:\Tools\grok-mcp\`
   （配置里写的是这个路径，移走就找不到）
2. 确认电脑上有 Python 3.9+：终端跑 `python --version`，没有就去 python.org 装，
   装的时候勾上 "Add Python to PATH"。如果 `python` 不可用，用 `py -3` 代替下面命令里的 `python`
3. 设密钥为 Windows 用户环境变量（只设一次，重启终端生效）：
   - 图形界面：设置 → 系统 → 关于 → 高级系统设置 → 环境变量 → 用户变量 → 新建，
     变量名 `XAI_API_KEY`，值粘贴 key
   - 或者 PowerShell（当前用户永久生效）：
     ```powershell
     [Environment]::SetEnvironmentVariable("XAI_API_KEY", "xai-...", "User")
     ```
   - **不要把 key 写进任何配置文件**。下面 Codex 的配置用 `env_vars` 只传变量名，
     不写值。

## 3. 挂给 Codex

终端跑（一行，路径换成你放文件的地方）：

```powershell
codex mcp add grok -- python C:\Tools\grok-mcp\grok_mcp_server.py
```

然后打开 `~/.codex/config.toml`，在 `[mcp_servers.grok]` 下面加一行，
让 Codex 把你设好的环境变量传给服务器（不加这行，服务器拿不到 key）：

```toml
[mcp_servers.grok]
command = "python"
args = ["C:\\Tools\\grok-mcp\\grok_mcp_server.py"]
env_vars = ["XAI_API_KEY"]
```

也可以直接把上面这段粘进 `config.toml`（完整片段见 `config-codex.toml`）。
改完重启 Codex，跑 `codex mcp list`，看到 grok 就是挂上了。
Codex 里调它的时候工具名叫 `ask_grok`（Codex 里显示成 `mcp__grok__ask_grok` 这类名字）。

## 4. 挂给 Antigravity

打开 `~/.gemini/config/mcp_config.json`，在 `mcpServers` 里加一段
（完整片段见 `config-antigravity.json`，路径换成你自己的）：

```json
{
  "mcpServers": {
    "grok": {
      "command": "python",
      "args": ["C:\\Tools\\grok-mcp\\grok_mcp_server.py"]
    }
  }
}
```

Antigravity 启动时会从自己的环境继承变量；如果它报 key 没设，
就先确认第 2 步的环境变量设好并重启了 Antigravity（只重启终端不够，
要重启 Antigravity 本身，让它重新读环境变量）。
实在不行，再在上面那段里加 `"env": {"XAI_API_KEY": "xai-..."}`——
但那样 key 就明文躺在这个文件里了，不推荐。

## 5. 验证（1 分钟）

在 Codex 里问一句：

> 用 ask_grok 问一下 Grok：Swift 的 SwiftData 在 iOS 26 上有什么已知坑？

应该直接返回 Grok 的回答。如果报错：

| 报错 | 意思 / 怎么办 |
| --- | --- |
| `XAI_API_KEY is not set` | 服务器没拿到 key。第 2 步的环境变量没生效：重启 Codex / Antigravity 再试；Codex 检查 `config.toml` 里有没有 `env_vars = ["XAI_API_KEY"]` |
| `xAI API returned HTTP 401` | key 不对或没充值。去 console.x.ai 检查 key 和余额 |
| `xAI API returned HTTP 429` | 触发限流，等一会儿再试；一直出现就去 Console 看套餐/限额 |
| `Could not reach the xAI API` | 网络问题，检查能不能打开 https://api.x.ai |

## 6. 怎么用（建议）

- 工具只有一个：`ask_grok(prompt, model="grok-4.7", system=None)`，
  一次问一个完整问题，答案一次性返回。
- 适合问 Grok 的：最新消息和变化（Grok 的强项）、第二意见、某个库或 API 的最新用法。
- 不适合：让它写整段代码（那是 DeepSeek Harness / Codex 的活）、来回多轮改稿
  （每次调用都是独立的一问一答，不保留上下文）。
- 成本：每次调用都花 xAI 的 API 额度，按 token 计费；问之前先想好问题，
  别拿它当搜索引擎一句一句喂。

## 7. 测试（在你电脑上也能跑，不用 key）

```powershell
cd C:\Tools\grok-mcp
python -m unittest test_grok_mcp_server -v
```

17 个测试应该全绿。HTTP 全是 mock 的，不会真的调 xAI、不会花钱。
真跑一次（会花一点 API 额度）：设好 `XAI_API_KEY` 后，在 Codex 里按第 5 步问一句就行。

## 安全说明

- key 只存在你电脑的环境变量里（以及 xAI Console 里），不进 git、不进聊天记录、
  不进任何配置文件。本项目没有也不需要任何仓库，两个文件直接放你电脑上就行。
- 服务器默认 `store: false`，不让 xAI 在服务端存这次问答。
- 这个工具只能问答，不能读写你电脑上的文件、不能执行命令；真正改代码的还是
  调它的那个 agent，按它自己的权限来。
