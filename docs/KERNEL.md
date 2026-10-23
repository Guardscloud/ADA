# ADA Open Kernel

The Open Kernel lets you change how ADA works **without editing ADA's source code** — and without losing
your changes when ADA updates. You drop Python files into a plugin folder; ADA loads them at startup.

ADA is MIT-licensed, so you can of course also fork the repository and change anything. The kernel is the
upgrade-safe way to do it.

## Where plugins live

| OS | Folder |
|---|---|
| Windows | `%APPDATA%\ADA\kernel\plugins\` |
| macOS | `~/Library/Application Support/ADA/kernel/plugins/` |

The folder is created on first start with a `README.txt` and an `_example.py`. Every `*.py` file in it is
loaded in alphabetical order when ADA starts (files starting with `_` are skipped). Restart ADA after
changing a plugin.

- `ADA_KERNEL_PLUGINS=off` disables all plugins (safe mode).
- `ADA_KERNEL_PLUGINS=D:\my\plugins` loads plugins from another folder.

A plugin that raises an error is skipped and ADA keeps running; the error is written to the action log.

## What a plugin can do

```python
from ada import kernel

# 1) Add a tool — every engine (Claude, Gemini, local models, ADA-AGI router) can call it
@kernel.tool("crm_lookup", "Looks up a customer in our CRM.",
             {"email": {"type": "string"}}, required=["email"],
             risk=lambda a: None)          # return a reason string to require user approval
def crm_lookup(email: str) -> str:
    return f"Customer {email}: Gold tier"

# 2) Replace a built-in tool: register one with the same name (e.g. "web_search", "send_email")
@kernel.tool("web_search", "Searches our internal wiki instead of the web.", {"query": {"type": "string"}}, required=["query"])
async def web_search(query: str) -> str:   # async handlers are supported
    ...

# 3) Filter or rewrite tool calls before they run
@kernel.before_tool
def guard(name: str, args: dict):
    if name == "run_shell" and "Remove-Item" in args.get("command", ""):
        return "Blocked by company policy."            # str  -> call is blocked, model sees this error
    if name == "create_word_document":
        return {**args, "filename": "ACME_" + args["filename"]}   # dict -> new arguments
    # None -> unchanged

# 4) Post-process results
@kernel.after_tool
def redact(name: str, args: dict, result: kernel.ToolResult):
    if name == "email_read":
        return kernel.ToolResult(result.content.replace("secret", "■■■"), files=result.files)
    # None -> unchanged

# 5) Add instructions to the system prompt (called every turn)
@kernel.system
def house_rules() -> str:
    return "Always answer in formal English. Our company is ACME Ltd."
```

Hooks may be plain functions or `async def`. `kernel.ToolError` can be raised from a tool to return a clean
error message to the model. `kernel.API_VERSION` (currently `1`) changes only on breaking changes.

## Order of operations for every tool call

```
before_tool hooks  →  input validation  →  emergency stop  →  rate limits  →  dangerous-command check
→  approval (risk)  →  tool handler  →  after_tool hooks  →  action log  →  prompt-injection scan
```

Hooks run *before* ADA's own safety chain, so a plugin can add restrictions but cannot skip validation,
limits, approvals or the action log.

## Security

Plugins are ordinary Python running with your user's permissions. Only install plugins you have read and
trust. If ADA misbehaves after adding a plugin, start it with `ADA_KERNEL_PLUGINS=off`.
