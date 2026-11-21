import argparse
import os
import queue
import sys
import threading

from .core import Library, preview
from .dialogue import HELP, Session
from .model import OllamaPlanner


def desktop(session):
    import tkinter as tk
    from tkinter import messagebox, simpledialog, ttk
    from tkinter.scrolledtext import ScrolledText

    root = tk.Tk()
    root.title("BBchannel · 对话配队")
    root.geometry("990x620")
    root.minsize(760, 460)
    root.columnconfigure(0, weight=1)
    root.rowconfigure(1, weight=1)
    bar = ttk.Frame(root, padding=(12, 10))
    bar.grid(row=0, column=0, sticky="ew")
    ttk.Label(bar, text="队伍预设").pack(side="left")
    template = ttk.Combobox(bar, state="readonly", width=33, values=list(session.library.presets()))
    template.pack(side="left", padx=8)
    if template["values"]:
        template.current(0)
    load_btn = ttk.Button(bar, text="加载", command=lambda: send("加载预设：" + template.get()))
    load_btn.pack(side="left")
    mode_label = "离线"
    ttk.Label(bar, text="本机模型").pack(side="left", padx=(12, 4))
    model_name = tk.StringVar(value=session.planner.model if session.planner else mode_label)
    model_picker = ttk.Combobox(bar, textvariable=model_name, width=16, values=[mode_label])
    model_picker.pack(side="left")
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
    status = tk.StringVar(value="本机模型 · " + session.planner.model if session.planner else "离线配队")
    ttk.Label(footer, textvariable=status).grid(row=1, column=0, sticky="w", pady=(4, 0))

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
        status.set("本机模型 · " + session.planner.model if session.planner else "离线配队")
        for widget in controls:
            widget.state(["!disabled"])
        template.configure(values=list(session.library.presets()))
        summary = answer.split("\n", 1)[0] if answer.startswith(("已更新草案", "已撤销上一条")) else answer
        display(chat, "助手　" + summary + "\n\n")
        display(draft, preview(session.cfg), clear=True)
        entry.focus_set()

    def send(text=None):
        nonlocal busy
        if busy:
            return "break"
        text = (entry.get() if text is None else text).strip()
        if text:
            name = model_name.get().strip()
            session.planner = OllamaPlanner(name) if name and name != mode_label else None
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
    controls = (load_btn, export_btn, undo_btn, send_btn, model_picker)
    entry.bind("<Return>", lambda event: send())
    display(chat, HELP + "\n\n")
    display(draft, preview(session.cfg), clear=True)
    entry.focus_set()
    root.mainloop()


def main():
    parser = argparse.ArgumentParser(description="BBchannel 对话配队助手")
    parser.add_argument("--cli", action="store_true", help="在终端对话")
    parser.add_argument("--model", default=os.getenv("BB_AGENT_MODEL"), help="可选的本机 Ollama 模型名")
    args = parser.parse_args()
    session = Session(Library(), OllamaPlanner(args.model) if args.model else None)
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
