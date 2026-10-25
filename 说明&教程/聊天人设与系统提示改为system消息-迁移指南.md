# 聊天人设与系统提示改为 system 消息：迁移指南

适用于把角色人设、用户设定、系统规则包装成 `user + assistant 确认回复` 的聊天项目。本文以 Python 为例，其他语言按相同的数据结构修改即可。

目标：在人设和系统提示的请求组装阶段使用 `role: "system"`，删除程序配套生成的“收到，我会……”确认消息，并适配不同模型渠道。

**范围说明：API 渠道可以发送原生系统消息；本文涉及的现有 CLI 文本接入仍只是文本分区，不会因此获得原生 system 角色。**

## 1. 改动前后是什么样

旧方式把设定伪装成了一轮对话：

```python
messages = [
    {"role": "user", "content": "[角色设定]\n你是一位温和的旅行伙伴。"},
    {"role": "assistant", "content": "收到，我会按照设定扮演角色。"},
    {"role": "user", "content": "[系统提示]\n回答简洁，保留必要信息。"},
    {"role": "assistant", "content": "收到，我会遵循这些规则。"},
    {"role": "user", "content": "今天去哪儿玩？"},
]
```

改为：

```python
messages = [
    {"role": "system", "content": "[角色设定]\n你是一位温和的旅行伙伴。"},
    {"role": "system", "content": "[系统提示]\n回答简洁，保留必要信息。"},
    {"role": "user", "content": "今天去哪儿玩？"},
]
```

`[系统提示]` 这样的文字标签可以保留，方便阅读和调试。真正决定 API 消息角色的是 `role` 字段。

只修改程序从配置中读取并主动插入的设定。真实用户发言、真实模型回复、搜索结果和历史聊天不能因为含有“系统提示”或“收到”几个字就被升级或删除。

## 2. 修改私聊和群聊的前缀组装

找到读取人设和系统提示的位置，将相应的 `user` 改为 `system`，并删除紧随其后的、程序硬编码的确认回复。

例如，原有逻辑改成下面这样。这里的 `wb` 代表项目自己的设定配置，`history` 代表已经构建好的对话历史：

```python
ai_name = wb.get("ai_name") or "AI"
user_name = wb.get("user_name") or "用户"
prefix = []

if wb.get("ai_persona"):
    prefix.append({
        "role": "system",
        "content": f"[系统设定 - {ai_name}人设]\n{wb['ai_persona']}",
    })

if wb.get("user_persona"):
    prefix.append({
        "role": "system",
        "content": f"[系统设定 - {user_name}信息]\n{wb['user_persona']}",
    })

if wb.get("system_prompt") and wb.get("system_prompt_enabled", True):
    prefix.append({
        "role": "system",
        "content": f"[系统提示]\n{wb['system_prompt']}",
    })

messages = prefix + history
```

名称仍从配置读取，系统提示的启用开关也要保留。如果某个角色原本有自己独立的人设来源，继续使用它原来的来源；不要给所有角色套同一份人设。

不要只改普通发送入口。项目如果分别构建以下请求，也需要检查对应前缀：

- 私聊正常发送、编辑后重发、主动发言。
- 群聊各个角色、第二角色的独立私聊。
- 搜索、记忆查询、查看动态、查看图片等操作完成后的后续回复。

若后续内容的插入位置原来写死为“前六条消息之后”，也要同步调整。删除确认消息后，前缀长度会变短，优先使用 `len(prefix)`。

本次 AionsHome 修改只删除**人设、用户设定和系统提示对应的确认消息**。能力说明、时间、设备状态、记忆等其他注入内容及其确认消息，没有在这次修改中统一迁移。

## 3. 按接入协议适配发送层

决定格式的是渠道使用的协议，不只是模型名称。同一个 Gemini 模型，通过官方原生 API、中转站或 CLI 接入，可能需要三种不同处理。

| 接入渠道 | 发送方式 | 是否为原生系统指令 |
| --- | --- | --- |
| OpenAI 兼容 Chat Completions API | `messages` 中保留 `role: "system"` | 请求侧是；中转站的上游转换由中转站负责 |
| Gemini 原生 GenerateContent API | 从对话中提取 system，放入 `systemInstruction` | 是 |
| 本项目现有 agy / Gemini / Codex CLI 文本接入 | 格式化为 `[System Instruction]` 文本分区 | 否，仍是 CLI 输入文本 |

### OpenAI 兼容中转站

沿用原有多模态转换逻辑，确保它保留传入的角色即可。不要把所有非 `assistant` 的消息都强制改成 `user`。

```python
api_messages = build_multimodal_messages(messages)
payload = {
    "model": model,
    "messages": api_messages,
    "stream": True,
}
```

这里的 `build_multimodal_messages` 是项目原有的文本、图片、音频等附件转换函数，不是需要额外安装的库。对文本消息，它应保留 `{"role": m["role"], "content": m["content"]}`。

即使中转站最终调用 Gemini，只要它对外提供 OpenAI 兼容接口，客户端仍按该接口发送 `system`。不要仅根据模型名中有 `gemini`，就把请求改成 Google 原生格式。

不同中转站可能有自己的限制，需要实际验证所使用的线路。若某站明确只允许一条系统消息，可以在该站的适配层合并开头连续的系统消息，保持内容顺序和分隔；不要为此改写真实聊天历史。

### Gemini 官方原生 API

这类接口的对话使用 `user` / `model`，系统指令另放在顶层 `systemInstruction`。需要同时做两件事：

1. 构建 `contents` 时跳过 `system`。
2. 将这些 `system` 内容放入 `systemInstruction.parts`。

下面是可独立运行的纯文本示例：

```python
def build_gemini_payload(messages):
    contents = []
    system_parts = []

    for message in messages:
        role = message["role"]
        content = message["content"]
        if role == "system":
            if content:
                system_parts.append({"text": content})
            continue

        contents.append({
            "role": "model" if role == "assistant" else "user",
            "parts": [{"text": content}],
        })

    payload = {"contents": contents}
    if system_parts:
        payload["systemInstruction"] = {"parts": system_parts}
    return payload


sample = [
    {"role": "system", "content": "角色设定"},
    {"role": "system", "content": "回答规则"},
    {"role": "user", "content": "你好"},
    {"role": "assistant", "content": "你好呀"},
]
payload = build_gemini_payload(sample)
assert payload["systemInstruction"]["parts"] == [
    {"text": "角色设定"}, {"text": "回答规则"},
]
assert [item["role"] for item in payload["contents"]] == ["user", "model"]
```

示例仅处理本指南中的文本 `system/user/assistant`。实际项目已有图片、音频、工具调用等处理时，要保留原有分支、附件处理和生成参数，只加入系统消息的分流逻辑。

特别检查旧代码中的这一类映射：

```python
role = "user" if message["role"] == "user" else "model"
```

如果没有在它之前跳过 `system`，新增的系统设定就会被误发成模型过去说过的话。

字段格式可参考 [Google 官方 GenerateContent 文档](https://ai.google.dev/api/generate-content?hl=zh-cn)。

### agy CLI、Gemini CLI、Codex CLI

本项目现有接入会先把消息列表压成一段文本，大致如下：

```text
[System Instruction]
角色设定

回答规则

[User]
你好

[Assistant]
```

这能保留设定内容，但标签本身不产生 API 原生的消息权限。以本项目 agy 接入为例，整段文本最终仍通过 `event: "user"` 提交。

如果原有 CLI 格式化器会把开头的“配置 user + 假确认 assistant”收编到系统分区，新加入的显式 `system` 不应让这个扫描提前停止。修改原则是：

1. 从消息开头顺序扫描，遇到显式 `system` 就收集其内容并继续。
2. 遇到程序生成的旧配置与确认消息对，也按原有逻辑收集配置并跳过确认。
3. 遇到第一条普通对话后停止收编，剩余对话按原有逻辑处理。
4. 保持收集内容的顺序，并保留原有图片、音频及附件路径处理。

若要让某条 CLI 线路获得真正独立的指令通道，需要再核查该 CLI 的原生参数、代理配置或协议支持。这是另一项接入改造，不能通过增加 `[System Instruction]` 标签实现。

## 4. 修正“已经有人设”的判断

有些项目在发送前会兜底补充角色人设。下面的旧判断会产生问题：

```python
has_persona = any(m["role"] == "system" for m in messages)
```

用户资料或回答规则现在也是 `system`，它们的存在不代表角色人设已经加入。

对于人设单独成条、允许带一个标题的项目，可以像本次修改一样检查实际人设内容：

```python
persona = read_persona()  # 替换为项目原有的人设读取函数
has_persona = any(
    message["role"] == "system"
    and (
        (message.get("content") or "") == persona
        or (message.get("content") or "").endswith("\n" + persona)
    )
    for message in messages
)
if persona and not has_persona:
    messages = [{"role": "system", "content": persona}] + messages
```

该判断适用于文本人设单独成条的结构。如果项目已把人设和多种规则合并成一条消息，应在组装阶段明确记录是否已加入人设，不要机械照搬这个后缀匹配。

## 5. 最小验收与生效方式

先在发送到模型之前捕获请求体，用模拟模型检查以下行为：

- 私聊、群聊的人设与用户设定为 `system`，内容完整，相关假确认消失。
- 系统提示关闭时不发送对应规则；空人设不会产生空的占位消息。
- 真实历史消息的角色、顺序及附件不变。
- Gemini 的 `contents` 中没有系统设定，设定位于 `systemInstruction`。
- 只有用户资料的 `system` 时，兜底逻辑仍会补人设；已经有人设时不会重复补充。
- CLI 保留设定内容，原有配置分区整理和附件处理仍然正常。

再对实际使用的渠道各发一条简短消息，检查是否报角色格式错误，以及人设表现是否符合预期。模拟测试不能证明第三方中转站内部一定正确转换了请求。

这是请求组装代码的修改，不需要删除聊天记录或重建数据库。若项目不是自动热重载，部署代码后需要重启后端。角色与规则的约束方式变化后，实际回复风格可能略有变化，尤其是在设定之间存在冲突时。

## 6. AionsHome 本次修改位置

其他项目可按职责寻找对应文件，不必采用相同文件名。

| 文件 | 修改内容 |
| --- | --- |
| `aion-chat/routes/chat.py` | 主角色私聊、编辑重发、主动发言和后续回复的设定前缀 |
| `aion-chat/chatroom.py` | 主角色群聊、第二角色群聊及私聊；第二角色人设兜底检查 |
| `aion-chat/routes/chatroom.py` | 聊天室相关操作完成后的后续回复前缀 |
| `aion-chat/ai_providers.py` | Gemini 原生系统字段适配；CLI 前缀扫描兼容显式 system |
| `aion-chat/test_system_message_roles.py` | 消息角色、内容保留、渠道转换及人设去重的回归测试 |
| `aion-chat/antigravity_image_hook.py` | 后续读图修复：按 UTF-8 解析 AGY 权限钩子的标准输入 |
| `aion-chat/test_antigravity_cli.py` | 后续读图修复：真实钩子子进程的编码及附件权限回归测试 |

system 迁移共删除上述入口中 37 处程序生成的人设或规则确认消息。包含新增用例在内的 51 项回归测试通过，随后加强新增用例中的内容保留断言，5 项新增测试再次通过。该阶段使用模拟模型，未逐条真实调用中转站，也未自动重启服务。后续 AGY 读图修复及真实模型验证见下一节。

## 7. 补充修复：Windows 下 AGY 图片权限校验失败

### 报错与原因

如果读图出现以下报错，可以检查图片权限钩子的输入编码：

```text
[AntigravityCLI错误] AGY 图片读取失败：
tool call denied by pre-tool hook:
Image attachment permissions could not be verified.
```

本次定位到的问题是：AGY 通过标准输入发送 UTF-8 JSON，而 Windows 上运行钩子的 Python 默认文本输入编码是 GBK。旧代码用 `json.load(sys.stdin)` 读取，遇到中文或表情可能解码失败，或者把中文路径解码成错误内容，导致合法附件也无法通过校验。

钩子事件不仅包含图片路径，还可能包含 `lastUserInput` 等字段。因此，即使图片文件名全是英文，中文聊天内容也可能触发问题。

这是图片权限钩子的编码兼容问题，**不需要撤回 system 消息迁移**。上述报错是一个通用兜底提示；其他项目若修正编码后仍失败，也要检查附件清单是否存在、JSON 是否有效，以及钩子是否读取了本次请求对应的清单。

### 最小修改

找到接收 AGY `PreToolUse` 事件的 Python 脚本。本项目是 `aion-chat/antigravity_image_hook.py`。

修改前：

```python
event = json.load(sys.stdin)
```

修改后：

```python
# AGY sends UTF-8 JSON; Windows text stdin may default to GBK.
event = json.loads(sys.stdin.buffer.read().decode('utf-8-sig'))
```

从二进制标准输入读取，再显式解码，避免依赖 Windows 的默认代码页。`utf-8-sig` 可以处理普通 UTF-8，也兼容开头带 BOM 的 UTF-8。

本项目修复后的脚本入口如下；`image_permission_decision` 是原有的附件白名单判断函数，保持不变：

```python
if __name__ == '__main__':
    try:
        event = json.loads(sys.stdin.buffer.read().decode('utf-8-sig'))
        image_paths = json.loads(
            Path(sys.argv[1]).read_text(encoding='utf-8')
        )
        decision = image_permission_decision(event, image_paths)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError):
        reason = 'Image attachment permissions could not be verified.'
        decision = {
            'decision': 'deny', 'allowTool': False,
            'reason': reason, 'denyReason': reason,
        }
    print(json.dumps(decision))
```

保留脚本已有的 `json`、`sys` 和 `Path` 导入，以及以下权限行为：只允许 `view_file` 读取本次附件白名单中的路径；其他文件、其他工具及无法解析的输入继续拒绝。不要通过关闭钩子或无条件返回 `allow` 来处理这个报错。

### 如何验证

1. **真实子进程编码测试**：以 `PYTHONIOENCODING=gbk`、`PYTHONUTF8=0` 启动钩子，用字节输入传入 `json.dumps(event, ensure_ascii=False).encode('utf-8')`。事件应包含中文路径，以及带中文或表情的 `lastUserInput`。白名单内的图片应返回 `allow`，白名单外的路径仍应返回 `deny`。
2. **走一次实际 AGY 读图**：使用简单的临时测试图片，例如左边红色三角形、右边蓝色圆形，让模型描述颜色和形状，确认真实 CLI、钩子和图片读取都成功。

只直接调用 `image_permission_decision()` 无法发现标准输入的编码问题，因为数据在进入函数前就可能已经解析失败。

测试时还要确保代理配置、钩子命令和附件清单属于同一个工作目录。本次曾遇到临时子目录继承父目录的同名钩子，结果钩子去读取父目录的附件清单，造成额外的校验失败。若使用隔离目录，应确认实际生效的钩子路径；若使用正在运行的聊天工作目录，应避开并发读图请求，避免覆盖共享附件清单。

本次修复后，AGY 与 system 消息相关的 **29 项回归测试通过**；使用真实 AGY 线路读取临时图片，模型正确回答了“左边红色三角形、右边蓝色圆形”。验证没有发送个人聊天图片。

### 生效方式

本项目每次工具调用都会新启动 Python 钩子进程，因此修改脚本后，**下一次图片读取即可生效，无需重启后端**。这与前面请求组装代码的部署要求不同。其他项目如果会缓存或复制钩子脚本，需要同步更新实际执行的那份脚本。
