"""命令行入口。"""

from __future__ import annotations

import os
import re
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from . import __version__, config

app = typer.Typer(
    help="jiyao 纪要：本地录音、中文转写、说话人区分，一键生成会议纪要。",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
err = Console(stderr=True)

DEFAULT_ROOT = Path.home() / "Documents" / "jiyao"
MINUTES = "纪要.md"
TRANSCRIPT_MD = "转写.md"
TRANSCRIPT_JSON = "transcript.json"

HotwordsOpt = Annotated[str | None, typer.Option("--hotwords", "-w", help="热词，逗号分隔：人名、项目代号、专有名词")]
SpeakersOpt = Annotated[
    str | None, typer.Option("--speakers", "-s", help="按发言先后给说话人命名，逗号分隔，如 张三,李四")
]
DateOpt = Annotated[str | None, typer.Option("--date", "-d", help="会议日期 YYYY-MM-DD，用于换算'下周三'这类相对时间")]
PushOpt = Annotated[bool, typer.Option("--push", help="生成后推送到配置好的飞书/钉钉/企业微信群")]
NoSummaryOpt = Annotated[bool, typer.Option("--no-summary", help="只转写，不调用大模型")]


def _split(s: str | None) -> list[str]:
    return [x for x in re.split(r"[,，、\s]+", s or "") if x] if s else []


def _speaker_names(s: str | None) -> list[str]:
    # 允许留空位：",李四" 表示第一位保留默认名
    return [x.strip() for x in re.split(r"[,，、]", s)] if s else []


def _parse_date(s: str | None, fallback: date) -> date:
    if not s:
        return fallback
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise typer.BadParameter(f"日期格式应为 YYYY-MM-DD：{s}")


def _fail(msg: str) -> None:
    err.print(f"[red]✗[/red] {msg}")
    raise typer.Exit(1)


def _process(
    audio: Path,
    out_dir: Path,
    hotwords: list[str],
    speakers: list[str],
    meeting_date: date,
    summary: bool,
    push: bool,
) -> None:
    from .asr import transcribe

    cfg = config.load()
    out_dir.mkdir(parents=True, exist_ok=True)
    words = cfg.hotwords + hotwords

    with console.status("加载识别模型（首次运行会从 ModelScope 下载约 2 GB）…"):
        from .asr import _load_model

        _load_model()
    with console.status(f"转写中：{audio.name}"):
        tr = transcribe(audio, hotwords=words)
    if not tr.sentences:
        _fail("没有识别出任何语音")
    tr.rename(speakers)
    tr.save(out_dir / TRANSCRIPT_JSON)
    (out_dir / TRANSCRIPT_MD).write_text(tr.to_markdown(), encoding="utf-8")
    console.print(
        f"[green]✓[/green] 转写完成：{len(tr.turns())} 段发言，{len(tr.speakers_in_order())} 位说话人 → {out_dir / TRANSCRIPT_MD}"
    )

    if summary:
        _summarize_and_save(tr, out_dir, meeting_date, push)


def _summarize_and_save(tr, out_dir: Path, meeting_date: date, push: bool) -> None:
    from .summarize import summarize

    cfg = config.load()
    console.print(f"[dim]用 {cfg.llm.model} 生成纪要…[/dim]")
    try:
        minutes = summarize(
            tr,
            cfg.llm,
            meeting_date=meeting_date,
            on_token=lambda t: console.out(t, end="", highlight=False),
            on_stage=lambda s: console.print(f"[dim]{s}…[/dim]"),
        )
    except Exception as e:  # noqa: BLE001 - LLM 侧错误五花八门，统一提示
        console.print()
        _fail(f"生成纪要失败：{e}\n转写已保存，配置好后可运行 `jiyao summarize {out_dir}` 重试")
    console.print()
    path = out_dir / MINUTES
    path.write_text(minutes, encoding="utf-8")
    console.print(f"[green]✓[/green] 纪要 → {path}")
    if push:
        _push(minutes, cfg)


def _push(minutes: str, cfg: config.Config) -> None:
    from .push import detect, send

    if not cfg.webhook:
        _fail("没有配置 webhook：在配置文件 [push] 里填 webhook，或设置环境变量 JIYAO_WEBHOOK")
    try:
        send(cfg.webhook, minutes, cfg.webhook_secret)
    except Exception as e:  # noqa: BLE001
        _fail(f"推送失败：{e}")
    platform = {"feishu": "飞书", "dingtalk": "钉钉", "wecom": "企业微信"}[detect(cfg.webhook)]
    console.print(f"[green]✓[/green] 已推送到{platform}群")


@app.command()
def record(
    title: Annotated[str, typer.Option("--title", "-t", help="会议名称，用作文件夹名")] = "会议",
    out: Annotated[
        Path | None, typer.Option("--out", "-o", help=f"输出目录，默认 {DEFAULT_ROOT}/<时间>_<名称>")
    ] = None,
    no_system: Annotated[bool, typer.Option("--no-system", help="不录系统声音（线下会议）")] = False,
    no_mic: Annotated[bool, typer.Option("--no-mic", help="不录麦克风（只旁听线上会议）")] = False,
    no_process: Annotated[bool, typer.Option("--no-process", help="只录音，不转写")] = False,
    hotwords: HotwordsOpt = None,
    speakers: SpeakersOpt = None,
    no_summary: NoSummaryOpt = False,
    push: PushOpt = False,
) -> None:
    """开始录音，按 Ctrl+C 结束，然后自动转写并生成纪要。"""
    from .record import record_until_interrupt, system_audio_supported

    if no_system and no_mic:
        _fail("系统声音和麦克风不能都关掉")
    started = datetime.now()
    out_dir = out or DEFAULT_ROOT / f"{started:%Y-%m-%d_%H%M}_{title}"
    system = not no_system
    if system and not system_audio_supported():
        console.print("[yellow]![/yellow] 录系统声音需要 macOS 15+ 和 Xcode Command Line Tools，本次只录麦克风")
        system = False

    sources = "+".join(n for n, on in (("系统声音", system), ("麦克风", not no_mic)) if on)
    console.print(f"[bold red]●[/bold red] 录音中（{sources}），按 [bold]Ctrl+C[/bold] 结束")
    try:
        with console.status("") as status:
            audio, error = record_until_interrupt(
                out_dir,
                system_audio=system,
                mic=not no_mic,
                on_tick=lambda s: status.update(f"已录 {int(s) // 60:02d}:{int(s) % 60:02d}"),
            )
    except RuntimeError as e:
        _fail(str(e))
    final = audio.with_name("录音.m4a")
    audio.rename(final)
    if error:
        console.print(f"[yellow]![/yellow] 录音提前中断：{error}，已保存录到的部分")
    console.print(f"[green]✓[/green] 录音 → {final}")

    if not no_process:
        _process(final, out_dir, _split(hotwords), _speaker_names(speakers), started.date(), not no_summary, push)


@app.command()
def run(
    audio: Annotated[
        Path,
        typer.Argument(
            help="音频或视频文件：wav/mp3/m4a/mp4…（腾讯会议、飞书的录制文件都行）", exists=True, dir_okay=False
        ),
    ],
    out: Annotated[Path | None, typer.Option("--out", "-o", help="输出目录，默认与音频同名的文件夹")] = None,
    hotwords: HotwordsOpt = None,
    speakers: SpeakersOpt = None,
    meeting_date: DateOpt = None,
    no_summary: NoSummaryOpt = False,
    push: PushOpt = False,
) -> None:
    """处理已有的录音文件：转写 + 生成纪要。"""
    out_dir = out or audio.with_suffix("")
    if out_dir == audio:
        out_dir = audio.with_name(audio.name + "_纪要")
    file_day = datetime.fromtimestamp(audio.stat().st_mtime).date()
    _process(
        audio,
        out_dir,
        _split(hotwords),
        _speaker_names(speakers),
        _parse_date(meeting_date, file_day),
        not no_summary,
        push,
    )


@app.command()
def summarize(
    target: Annotated[Path, typer.Argument(help="会议文件夹，或其中的 transcript.json", exists=True)],
    speakers: SpeakersOpt = None,
    meeting_date: DateOpt = None,
    push: PushOpt = False,
) -> None:
    """重新生成纪要：改了说话人名字、换了模型之后用。"""
    from .transcript import Transcript

    path = target / TRANSCRIPT_JSON if target.is_dir() else target
    if not path.exists():
        _fail(f"找不到 {path}")
    tr = Transcript.load(path)
    if speakers:
        tr.rename(_speaker_names(speakers))
        tr.save(path)
        (path.parent / TRANSCRIPT_MD).write_text(tr.to_markdown(), encoding="utf-8")
    file_day = datetime.fromtimestamp(path.stat().st_mtime).date()
    _summarize_and_save(tr, path.parent, _parse_date(meeting_date, file_day), push)


@app.command()
def push(
    target: Annotated[Path, typer.Argument(help="纪要 Markdown 文件，或会议文件夹", exists=True)],
) -> None:
    """把纪要推送到飞书/钉钉/企业微信群机器人。"""
    path = target / MINUTES if target.is_dir() else target
    if not path.exists():
        _fail(f"找不到 {path}")
    _push(path.read_text(encoding="utf-8"), config.load())


@app.command("config")
def show_config() -> None:
    """生成配置文件模板，并显示当前生效的配置。"""
    path = config.write_example()
    cfg = config.load()
    key = cfg.llm.api_key
    masked = f"{key[:5]}…{key[-4:]}" if len(key) > 12 else ("未设置" if not key else "已设置")
    console.print(f"配置文件：{path}")
    console.print(f"  LLM：{cfg.llm.model} @ {cfg.llm.base_url}（key {masked}）")
    console.print(f"  热词：{'、'.join(cfg.hotwords) or '无'}")
    console.print(f"  推送：{cfg.webhook or '未配置'}")


@app.command()
def doctor() -> None:
    """检查运行环境。"""
    from .record import system_audio_supported

    ok = True

    def check(name: str, passed: bool, hint: str = "") -> None:
        nonlocal ok
        ok &= passed
        mark = "[green]✓[/green]" if passed else "[red]✗[/red]"
        console.print(f"{mark} {name}" + (f"  [dim]{hint}[/dim]" if hint and not passed else ""))

    check("ffmpeg", shutil.which("ffmpeg") is not None, "brew install ffmpeg / apt install ffmpeg")
    sys_audio = system_audio_supported()
    console.print(
        ("[green]✓[/green]" if sys_audio else "[yellow]![/yellow]")
        + " 录系统声音"
        + ("" if sys_audio else "  [dim]需要 macOS 15+ 和 swiftc（xcode-select --install）；不影响处理录音文件[/dim]")
    )
    cache = Path(os.environ.get("MODELSCOPE_CACHE", Path.home() / ".cache" / "modelscope"))
    models = any(cache.glob("**/*speech_seaco_paraformer*"))
    console.print(
        ("[green]✓[/green]" if models else "[yellow]![/yellow]")
        + " 识别模型"
        + ("" if models else "  [dim]首次转写时自动下载[/dim]")
    )

    cfg = config.load()
    if cfg.llm.api_key or "11434" in cfg.llm.base_url or "localhost" in cfg.llm.base_url:
        from openai import OpenAI

        try:
            OpenAI(base_url=cfg.llm.base_url, api_key=cfg.llm.api_key or "ollama", timeout=10).models.list()
            check(f"大模型 {cfg.llm.model}", True)
        except Exception as e:  # noqa: BLE001
            check(f"大模型 {cfg.llm.model}", False, f"连不上 {cfg.llm.base_url}：{e}")
    else:
        check("大模型", False, "未配置 API key，运行 `jiyao config`")
    raise typer.Exit(0 if ok else 1)


@app.command()
def version() -> None:
    """显示版本。"""
    console.print(f"jiyao {__version__}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
