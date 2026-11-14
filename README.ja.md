# Omni Flash Video Lab

[English](./README.md) · [日本語](./README.ja.md) · [中文](./README.zh-CN.md)

**Gemini Omni Flash** 向けのローカル / Cloud Run 実験台です。既定モデルは正式版 **Omni 1.2 Flash**（`gemini-omni-1.2-flash`）。UI から **1.1 Flash Preview** や任意のモデル ID にも切り替えられます。ショットリストと参照画像／参照動画を渡し、Google Cloud **Agent Platform Interactions API** で生成・多轮編集・延長・拡大を行います。

能力とプロンプトの詳細は [learning_guide.md](./learning_guide.md)（中国語）。画面内の **API ガイド**（`/docs` でも開けます）に各機能、モデル別の上限、公式ページをまとめています。この README は起動・デプロイ・API の使い方です。

### 音声の設定

スタジオには **人の声、BGM、環境音・効果音**（初期値オン）と **字幕**（初期値オフ）があります。Omni のネイティブ音声パラメータではなく、プロンプトによる指示です。**元のプロンプトの明示的な要望・禁止を常に優先**します。指定がなければ、オンの項目は場面や雰囲気から推測して生成し、オフの項目は省きます。指定した台詞・言語・音楽の詳細を保持し、プロンプト補助のオン・オフに関係なく適用します。

設定はモードごとの下書き、タスク履歴、エクスポートに保存し、「この設定を使用」で復元できます。4 種の生成と延長の追加部分が対象です。編集では台詞の変更はできず、アップスケールはテキスト指示を受け付けません。モデルの遵守は保証されず、生成後にアプリが音声を強制的に消去・差し替えすることはありません。

`POST /api/generate` の `audio_preferences: {"speech": true, "music": true, "effects": true}` と `no_subtitles: true` は最終プロンプトへ変換されます。Omni の未対応フィールドとして送信しません。詳細画面で実際のプロンプトを確認できます。`audio_preferences` がない場合は旧 `generate_audio` / `no_bgm` を使用します。隠れた BGM 禁止と引用符だけに依存する台詞制限は削除しました。

公式：[音声プロンプトガイド](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/video-gen-prompt-guide)。


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
| 入力 | **任意長** | **≤ 30 秒** で遮断 |
| **見る**（約 10 秒） | 次の展開を決める**文脈**として末尾約 10 秒だけ使う | 同様 |
| **残す**（約 30 秒） | 出力はソース末尾約 30 秒 + 新セグメント。それより前は切り捨て | 同様 |
| 1 回の追加 | **3–10 秒**（15 秒ではない） | 同じ |
| 出力 | およそ ≤ **40 秒**（末 30 + 新 ≤10） | 同じ |

60 秒ソースに 10 秒延長すると約 **40 秒**（末 30 + 新 10）であり、70 秒にはなりません。「末尾 10 秒」は**文脈窓**であり、「ファイルにソースが 10 秒しか残らない」という意味ではありません。`15+15+15=45` では設計しないでください。

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

実行中の生成は開始したインスタンスがポーリングするため、`--no-cpu-throttling` と `--max-instances 1` を維持します。履歴は GCS に保存されるので、ゼロスケールや再デプロイ後も残ります。

## スタジオ・作品集・タスク履歴

- **スタジオ**：8 つの機能それぞれが自分のパラメータだけを表示し、下書きを個別に保持します。再読込後も下書きと開いていた作品が戻ります。
- **プレビュー**：完成した動画から 編集を継続 / 編集 / 延長 / 高解像度化 にそのまま進めます。「この設定を使用」で当時の入力をスタジオに戻せます。入力、送信したプロンプト、同じ元動画から派生したバージョン（並べて再生）を確認できます。
- **作品集**：タイトル、コレクション、メモ、お気に入り、アーカイブ、復元できるごみ箱。
- **ワークスペース**：履歴はブラウザのワークスペース（http-only cookie）に紐付きます。「ワークスペース → 復元ファイルを書き出す」で別の端末から同じワークスペースを開けます。記録は `OUTPUT_GCS_URI/workspaces/`（`WORKSPACE_STORE_URI` で変更可）に保存されます。
- 表示言語：English / 日本語 / 中文。

## 既知の制限

- 非同期ジョブとして扱うこと。
- 音声ファイル入力なし。台詞編集不可。
- Extend は公式どおり：**入力は任意長**。`response_format.duration` が今回の追加秒数（3–10）。`aspect_ratio` はライブで拒否されるため送らない。長いソースを Demo 側で切り詰めない。
- 多輪 `previous_interaction_id` は Continue / Edit のみ。そのターンは `video_config.task` を送らない。Extend と Upscale は常に `gs://` と `task` を再送する。
- Upscale はテキスト不可。解像度は 360p→720p/1080p、720p→1080p/4k。1080p→4k は不可。Upscale 後の Edit/Extend は `gs://` を再送。
- 失敗時は Omni の `errors` 原文を `message` に出す。
- 素材の **fragmented / CMAF MP4** は progressive に remux（長さは変えない）。meta に `source_remuxed: true` が出ることがある。

詳細は [learning_guide.md](./learning_guide.md) を参照。
