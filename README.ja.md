# Omni Flash Video Lab

[English](./README.md) · [日本語](./README.ja.md) · [中文](./README.zh-CN.md)

**Gemini Omni Flash** 向けのローカル / Cloud Run 実験台です。既定モデルは正式版 **Omni 1.2 Flash**（`gemini-omni-1.2-flash`）。UI から **1.1 Flash Preview** や任意のモデル ID にも切り替えられます。ショットリストと参照画像／参照動画を渡し、Google Cloud **Agent Platform Interactions API** で生成・多轮編集・延長・拡大を行います。

能力とプロンプトの詳細は [learning_guide.md](./learning_guide.md)（中国語）。実行中の公式ドキュメントハブは `/docs`。この README は起動・デプロイ・API の使い方です。

## できること

| UI | API `task` | 内容 |
| --- | --- | --- |
| **Generate → Text** | `text_to_video` | テキストから新規クリップ |
| **Generate → First frame** | `image_to_video` | 1 枚の静止画を第 0 フレームに |
| **Generate → First + last** | `image_to_video` | 2 枚の間を補間 |
| **Generate → References** | `reference_to_video` | 参照画像最大 10、参照動画最大 3 |
| **Continue / Edit** | `edit` | 1.2 は多轮が既定。1.1 は毎回 `gs://` を再送 |
| **Extend** | `extend` | 3–10 秒を末尾に追加し結合クリップを返す。**1.2 の入力は任意長**、**本 Demo の 1.1 は ≤ 30 秒**。出力はおよそ ≤ **40 秒** |
| **Upscale** | `upscale` | 1.2 のみ。15 秒以内のクリップを高解像度化。テキスト不可 |

### Extend：1.1 と 1.2 の重要差

公式: [Extend videos](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/extend-videos)、[1.2 モデルカード](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/omni-1-2-flash)、[1.1 モデルカード](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/omni-1-1-flash)（Deprecated）。

| | 1.2 | 1.1（本 Demo） |
| --- | --- | --- |
| 入力 | **任意長**（末尾約 10 秒を文脈に使用） | **≤ 30 秒** で遮断 |
| 1 回の追加 | **3–10 秒**（15 秒ではない） | 同じ |
| 出力 | およそ ≤ **40 秒** | 同じ |

60 秒ソースに 10 秒延長しても約 40 秒です。`15+15+15=45` では設計しないでください。

## ローカル起動

```bash
cp .env.example .env
# GOOGLE_CLOUD_PROJECT / INPUT_GCS_URI / OUTPUT_GCS_URI を設定
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

http://127.0.0.1:8000 を開きます。生成は 1–3 分以上かかることがあります。

## Cloud Run

```bash
PROJECT_ID=your-gcp-project BUCKET=your-gcs-bucket ./deploy/cloud-run.sh
```

`JobStore` がプロセスメモリのため、`--no-cpu-throttling` と `--max-instances 1` を維持します。

## 既知の制限

- 非同期ジョブとして扱うこと。
- 音声ファイル入力なし。台詞編集不可。
- Extend は `aspect_ratio` を送ると拒否される。
- Upscale はテキスト不可。Upscale 後の Edit/Extend は `gs://` を再送。
- 失敗時は Omni の `errors` 原文を `message` に出す。

詳細は [learning_guide.md](./learning_guide.md) を参照。
