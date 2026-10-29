# 画布网关（canvas_gateway）

让 [infinite-canvas](https://github.com/basketikun/infinite-canvas) 无限画布直接用本地模型出图、出视频、配音，不花钱，也不走外网。

画布只会调用"生成接口"（OpenAI 风格的 API）。网关就是在本机冒充这种接口：画布发来的请求，被它转给本地的 ComfyUI 工作流、脚本或 TTS 服务，结果再交回画布。

```
无限画布（浏览器） ──/v1/...──▶ 画布网关 :7860 ──▶ ComfyUI :8189（Qwen、H3 工作流）
                                      ├──▶ 脚本（make_film.py 口播长片）
                                      └──▶ 本地 TTS :9880（IndexTTS2.5 / CosyVoice3）
```

## 能做什么

| 画布里的操作 | 网关接口 | 本地做什么 |
|---|---|---|
| 文本节点点「生图」 | `POST /v1/images/generations` | 跑出图工作流，等出图后直接返回 |
| 带参考图生图 | `POST /v1/images/edits` | 同上，参考图上传给 ComfyUI |
| 视频节点生成 | `POST /v1/videos` → 轮询 `GET /v1/videos/{id}` → `GET /v1/videos/{id}/content` | 排队跑视频工作流或口播脚本 |
| 音频节点生成 | `POST /v1/audio/speech` | 本地 TTS，一个音色对应一个模型 |

其他特点：

- **按需启动、闲置自动关。** ComfyUI 和 TTS 不用提前开：有任务时网关会启动它们，闲置 `idle_stop_minutes` 分钟后自动关掉。原本就开着的服务（比如 Mars 自己的 ComfyUI）只用不关。
- **显卡独占。** `exclusive_gpu` 打开时，跑 ComfyUI 任务前会先关掉网关自己启动的 TTS，反过来也一样，避免两个模型抢 16G 显存。
- **任务排队。** 所有任务共用一张显卡，按顺序一个个跑。
- **长视频不会超时。** 画布最多轮询 120 次就放弃。所以视频没跑完时，网关每次查询会先等 20 秒再回答，总共能等约 45 分钟。超过的话，在画布上点「获取任务状态」可以接着等。
- **重启不丢结果。** 每个任务的输入、工作流、日志和结果都存在 `work_dir/<任务号>/`。网关重启后，已完成的视频仍能取回。

## 安装（Windows）

1. **网关**：用 OpenMontage 自己的环境，不需要另装依赖（fastapi、uvicorn、requests 都已在 requirements.txt 里）。
2. **画布**：
   ```
   git clone https://github.com/basketikun/infinite-canvas H:\win-ai\infinite-canvas
   cd H:\win-ai\infinite-canvas\web
   bun install
   bun run build
   ```
   构建结果在 `web\dist`，由网关直接提供，所以不用单独开画布服务，也没有跨域问题。
3. **配置**：复制 `config.example.json` 为 `H:\win-ai\canvas_gateway\canvas_gateway.json`，填好路径和节点 ID（见下文）。
4. **启动**：
   ```
   cd H:\win-ai\OpenMontage
   .venv\Scripts\python.exe -m services.canvas_gateway.server --config H:\win-ai\canvas_gateway\canvas_gateway.json
   ```
   然后浏览器打开 http://127.0.0.1:7860。

## 画布里第一次要设置的

右上角「配置」，新建一个渠道：

- Base URL 填 `http://127.0.0.1:7860/v1`
- API Key 随便填，比如 `local`
- 点「拉取模型」，列出的就是配置文件里的本地模型
- 默认生图模型选「Qwen 出图 (image)」，默认视频模型选「H3 图生视频 (video)」或「H3 口播 (video)」，默认音频模型选一个配音音色

画布按模型名里的 image / video / tts 判断模型类型，所以模型名里要保留这几个词。

## 新手页面：一键出片

不想用画布时，浏览器打开 http://127.0.0.1:7860/easy 。页面上是几个大按钮，比如出图、图片变视频、数字人口播、配音。点一个按钮，放进素材，再点「开始」就行。

- 数字人口播可以直接上传录音，也可以打字：网关先用选中的音色配音，再接着出视频。
- 页面下方是最近的任务，显示排队位置和已用时间，完成后可以直接播放或下载。页面关了也不影响，任务照样在跑。
- 按钮来自配置里每个模型的 `easy` 字段，没有 `easy` 的模型只在画布里出现：
  - `button`、`icon`、`hint`、`minutes`：按钮名字、图标、说明，以及大概要几分钟。
  - `fields`：表单里要填的项，按顺序排列，类型有 `image`、`audio`、`video`、`speech`（录音或打字配音二选一）、`prompt`、`seconds`。每项可以写 `label`、`placeholder`、`optional`。
  - `sizes`：画面尺寸下拉框，例如 `[{"value": "1280x736", "label": "横屏 720p"}]`。
- 配音模型（`capability: "audio"`）会自动出现在口播的音色下拉框里。

## 配置文件

`services`：本地服务。字段如下：

- `url`、`health`：用来检查服务是否已开着。
- `start`、`cwd`、`env`：服务没开时怎么启动；不写 `start` 就只用、不启动。
- `start_timeout`、`idle_stop_minutes`：启动最多等多久，闲置多久后关掉。

`models`：画布里能选的模型，每个模型用一种适配器。

**`type: "comfyui"`**：填一个 API 格式工作流（在 ComfyUI 里用「导出 (API)」另存）。

- `output_node`：输出图片或视频的节点 ID。
- `inputs`：把画布传来的内容写进哪个节点的哪个输入，每项写成 `[节点ID, 输入名]`：
  - `prompt`：提示词
  - `seed`，或者 `seeds`（多个采样器时用）：随机种子
  - `width` / `height`：画面宽高
  - `frames`：帧数，由秒数按 `fps` 和 `frame_grid` 换算。H3 的帧数是 17k+5，例如 5 秒 = 124 帧
  - `images` / `audios` / `videos`：参考素材，按画布连接的顺序依次填入
- `fixed`：每次都固定写入的值，格式是 `{节点ID: {输入名: 值}}`。
- 尺寸限制：`default_size`（画布选「自动」时用）、`max_pixels`（超过就等比缩小）、`size_multiple`（宽高对齐到它的倍数）。
- 其他：`max_seconds`、`min_images` 等（缺少必需的参考素材时直接报错）、`timeout_seconds`。

**`type: "command"`**：跑一个脚本，适合口播长片这类需要多段工作流的任务。

- 命令里可以用这些占位符：
  - `{prompt}`、`{prompt_file}`：提示词，或保存提示词的文件
  - `{image1}`…、`{audio1}`…、`{video1}`…：参考素材
  - `{images_dir}`：参考图所在的文件夹
  - `{out}`：结果文件路径，脚本必须写出这个文件
  - `{job_dir}`：本任务的工作目录
  - `{width}`、`{height}`、`{seconds}`、`{seed}`
- 用 `services` 列出脚本要用到的服务，网关会先把它们启动。
- 脚本的输出写在 `log.txt`；失败时，日志末尾会显示在画布上。

**`type: "tts"`**：调用本地 TTS 的 `/tts` 接口，音色由模型的 `voice` 字段决定，画布里选的 alloy 等会被忽略。想用几个音色就建几个模型。

## 注意

- 出图请求是同步的，画布最多等 10 分钟。如果前面排着一条长视频，出图会等到超时；先等视频跑完再出图。
- 网关只监听 127.0.0.1，局域网里别的电脑连不上。
- 测试：`python -m pytest tests/services/test_canvas_gateway.py`
