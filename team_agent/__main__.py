import argparse
import os
import queue
import sys
import threading

from .core import Library, preview
from .dialogue import HELP, Session
from .model import BIGMODEL_MODEL, BigModelPlanner, OllamaPlanner, bigmodel_key


def desktop(session):
    import tkinter as tk
    from tkinter import messagebox, simpledialog, ttk
    from tkinter.scrolledtext import ScrolledText

    root = tk.Tk()
    root.title("BBchannel · 对话配队 v0.6")
    root.geometry("990x620")
    root.minsize(760, 460)
    root.columnconfigure(0, weight=1)
    root.rowconfigure(1, weight=1)
    header = ttk.Frame(root, padding=(12, 10))
    header.grid(row=0, column=0, sticky="ew")
    bar = ttk.Frame(header)
    bar.pack(fill="x")
    ttk.Label(bar, text="队伍预设").pack(side="left")
    template = ttk.Combobox(bar, state="readonly", width=33, values=list(session.library.presets()))
    template.pack(side="left", padx=8)
    if template["values"]:
        template.current(0)
    load_btn = ttk.Button(bar, text="加载", command=lambda: send("加载预设：" + template.get()))
    load_btn.pack(side="left")
    roster_btn = ttk.Button(bar, text="我的从者", command=lambda: send("我的从者"))
    roster_btn.pack(side="left", padx=(8, 2))
    find_btn = ttk.Button(bar, text="匹配预设", command=lambda: send("匹配预设"))
    find_btn.pack(side="left")
    model_bar = ttk.Frame(header)
    model_bar.pack(fill="x", pady=(8, 0))
    cloud = isinstance(session.planner, BigModelPlanner)
    local = isinstance(session.planner, OllamaPlanner)
    provider = tk.StringVar(value="bigmodel" if cloud else "ollama" if local else "offline")
    cloud_model = session.planner.model if cloud else BIGMODEL_MODEL
    key = tk.StringVar(value=session.planner.api_key if cloud else bigmodel_key())
    local_model = tk.StringVar(value=session.planner.model if local else os.getenv("BB_AGENT_MODEL", ""))
    radios = []
    for value, label in (("bigmodel", "智谱 API"), ("ollama", "本地 Ollama"), ("offline", "离线句式")):
        button = ttk.Radiobutton(model_bar, text=label, variable=provider, value=value,
                                 command=lambda: show_mode())
        button.pack(side="left", padx=(0, 6))
        radios.append(button)
    cloud_fields = ttk.Frame(model_bar)
    ttk.Label(cloud_fields, text=cloud_model + "  ·  API Key").pack(side="left", padx=(4, 5))
    key_entry = ttk.Entry(cloud_fields, textvariable=key, show="*", width=24)
    key_entry.pack(side="left")
    local_fields = ttk.Frame(model_bar)
    ttk.Label(local_fields, text="模型名").pack(side="left", padx=(4, 5))
    local_entry = ttk.Entry(local_fields, textvariable=local_model, width=22)
    local_entry.pack(side="left")
    panes = ttk.Panedwindow(root, orient="horizontal")
    panes.grid(row=1, column=0, sticky="nsew", padx=12)
    chat_panel, preview_panel = ttk.Frame(panes), ttk.Frame(panes)
    panes.add(chat_panel, weight=3)
    panes.add(preview_panel, weight=2)
    ttk.Label(chat_panel, text="对话").pack(anchor="w")
    ttk.Label(preview_panel, text="队伍与行动顺序").pack(anchor="w")
    chat = ScrolledText(chat_panel, width=42, wrap="word", padx=9, pady=8, state="disabled")
    draft = ScrolledText(preview_panel, width=30, wrap="word", padx=9, pady=8, state="disabled")
    for view in (chat, draft):
        view.pack(fill="both", expand=True)
    footer = ttk.Frame(root, padding=12)
    footer.grid(row=2, column=0, sticky="ew")
    footer.columnconfigure(0, weight=1)
    entry = ttk.Entry(footer, font=("Microsoft YaHei UI", 11))
    entry.grid(row=0, column=0, sticky="ew", ipady=4)
    status = tk.StringVar()
    ttk.Label(footer, textvariable=status).grid(row=1, column=0, sticky="w", pady=(4, 0))

    def show_mode():
        cloud_fields.pack_forget()
        local_fields.pack_forget()
        if provider.get() == "bigmodel":
            cloud_fields.pack(side="left")
            status.set("智谱 API · " + cloud_model)
        elif provider.get() == "ollama":
            local_fields.pack(side="left")
            status.set("本地 Ollama · " + (local_model.get().strip() or "请填写模型名"))
        else:
            status.set("离线配队")

    def display(view, value, clear=False):
        view.configure(state="normal")
        if clear:
            view.delete("1.0", "end")
        view.insert("end", value)
        view.configure(state="disabled")
        if not clear:
            view.see("end")

    results = queue.Queue()
    busy = False

    def worker(text):
        try:
            results.put(session.send(text))
        except Exception as exc:
            results.put(f"处理失败：{exc}")

    def receive():
        nonlocal busy
        try:
            answer = results.get_nowait()
        except queue.Empty:
            root.after(80, receive)
            return
        busy = False
        show_mode()
        for widget in controls:
            widget.state(["!disabled"])
        template.configure(values=list(session.library.presets()))
        summary = answer.split("\n", 1)[0] if answer.startswith(("已更新草案", "已撤销上一条")) else answer
        display(chat, "助手　" + summary + "\n\n")
        display(draft, session.preview(), clear=True)
        entry.focus_set()

    def send(text=None):
        nonlocal busy
        if busy:
            return "break"
        text = (entry.get() if text is None else text).strip()
        if text:
            if provider.get() == "bigmodel":
                session.planner = BigModelPlanner(key.get(), cloud_model)
            elif provider.get() == "ollama":
                session.planner = OllamaPlanner(local_model.get().strip())
            else:
                session.planner = None
            entry.delete(0, "end")
            display(chat, "你　" + text + "\n")
            busy = True
            status.set("正在处理…")
            for widget in controls:
                widget.state(["disabled"])
            threading.Thread(target=worker, args=(text,), daemon=True).start()
            root.after(80, receive)
        return "break"

    def export():
        if not session.cfg:
            messagebox.showinfo("导出预设", "请先加载或创建一支队伍。", parent=root)
            return
        name = simpledialog.askstring("导出到 settings", "新预设名称", parent=root,
                                      initialvalue=session.title + "_配队")
        if name:
            send("导出 " + name)

    export_btn = ttk.Button(bar, text="导出新预设", command=export)
    export_btn.pack(side="right")
    undo_btn = ttk.Button(bar, text="撤销", command=lambda: send("撤销"))
    undo_btn.pack(side="right", padx=6)
    send_btn = ttk.Button(footer, text="发送", command=send)
    send_btn.grid(row=0, column=1, padx=(8, 0))
    controls = (load_btn, roster_btn, find_btn, export_btn, undo_btn, send_btn, key_entry, local_entry, *radios)
    entry.bind("<Return>", lambda event: send())
    display(chat, HELP + "\n\n")
    display(draft, preview(session.cfg), clear=True)
    show_mode()
    entry.focus_set()
    root.mainloop()


def main():
    parser = argparse.ArgumentParser(description="BBchannel 对话配队助手")
    parser.add_argument("--cli", action="store_true", help="在终端对话")
    parser.add_argument("--provider", choices=("bigmodel", "ollama", "offline"), help="选择模型来源")
    parser.add_argument("--model", help="所选服务的模型名；单独使用时沿用 Ollama")
    args = parser.parse_args()
    legacy_local = args.model or (args.cli and os.getenv("BB_AGENT_MODEL"))
    provider = args.provider or ("ollama" if legacy_local else "bigmodel")
    if provider == "bigmodel":
        planner = BigModelPlanner(bigmodel_key(), args.model or BIGMODEL_MODEL)
    elif provider == "ollama":
        planner = OllamaPlanner(args.model or os.getenv("BB_AGENT_MODEL", ""))
    else:
        planner = None
    session = Session(Library(), planner)
    if not args.cli:
        desktop(session)
        return
    print(HELP)
    for line in sys.stdin:
        if line.strip() in ("退出", "exit"):
            break
        if line.strip():
            print(session.send(line), flush=True)


if __name__ == "__main__":
    main()
