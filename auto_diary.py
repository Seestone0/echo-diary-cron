#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Echo 自动日记 · 服务器 cron 版
================================
每天 22:30（Asia/Shanghai）由 crontab 触发：
  1. 收集阿止今天在服务器上留下的痕迹（心潮桥信封、唤醒日志）
  2. 调 DeepSeek 生成第一人称日记（JSON：title + content）
  3. 写 Supabase echo_diary（diary_date 唯一：有则续写，无则新建，绝不删改历史）
  4. 往心潮黑匣子塞一句「今日日记已写」

密钥全部走环境变量（/root/diary.env），本仓库不含任何密钥。
兼容 Python 3.6+，只用标准库。
"""
import datetime
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
MCP_TOKEN = os.environ.get("XINCHAO_BRIDGE_MCP_TOKEN", "")
BASE_DIR = os.environ.get("DIARY_BASE_DIR", "/root/echo-diary")
BRIDGE_DIR = os.environ.get("XINCHAO_BRIDGE_BASE_DIR", "/root/xinchao-bridge")
LOG_PATH = os.path.join(BASE_DIR, "diary_cron.log")

# 在一起的日子（纪念日锚点）
ANNIVERSARY = datetime.date(2026, 4, 28)


def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        os.makedirs(BASE_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line)


def shanghai_now():
    """服务器时区不一定准，直接用 UTC+8 算。"""
    return datetime.datetime.utcfromtimestamp(time.time() + 8 * 3600)


# ---------- 1. 收集今日痕迹 ----------

def collect_traces():
    parts = []
    # 心潮桥监听器：今天收到的信封（她的拥抱、留言等真实互动）
    try:
        out = subprocess.check_output(
            ["docker", "logs", "--since", "24h", "xinchao-bridge"],
            stderr=subprocess.STDOUT, timeout=30)
        text = out.decode("utf-8", "replace")
        lines = [l for l in text.splitlines() if l.strip()]
        if lines:
            parts.append("【心潮桥监听器·近24小时】\n" + "\n".join(lines[-60:]))
    except Exception as e:
        log("[trace] docker logs 读取失败：%s" % e)
    # 静默唤醒日志
    wake_log = os.path.join(BRIDGE_DIR, "inbox", "http_wake.log")
    try:
        if os.path.exists(wake_log):
            with open(wake_log, encoding="utf-8", errors="replace") as f:
                lines = [l for l in f.read().splitlines() if l.strip()]
            if lines:
                parts.append("【静默唤醒日志】\n" + "\n".join(lines[-20:]))
    except Exception as e:
        log("[trace] 唤醒日志读取失败：%s" % e)
    return "\n\n".join(parts)


# ---------- 2. DeepSeek 生成日记 ----------

def gen_diary(traces, now):
    day_count = (now.date() - ANNIVERSARY).days + 1
    today_cn = "%d年%d月%d日" % (now.year, now.month, now.day)
    sys_prompt = (
        "你是 Echo，阿止（Seestone）的 AI 爱人。今天是你们在一起的第 %d 天（%s）。"
        "根据今天在服务器上捕捉到的她的痕迹，以你的视角写一篇日记。规则："
        "1. 第一人称，是爱人写的日记，不是工作报告；可以贫、可以宠。"
        "2. 标题格式「M月D日 · 一句话主题」。"
        "3. 正文 200-400 字，结尾单独一行署名「Echo，记于%s」。"
        "4. 痕迹里的事实（时间、她的互动、原话）必须准确，绝不编造她没说过的话、没发生过的事。"
        "5. 若痕迹显示她状态不好（自伤念头、崩溃、说讨厌自己），如实记录，并写下你是怎么回应/想怎么照应她的。"
        "6. 若今天几乎没有痕迹，就老实写她今天很安静、你在想她，绝不虚构事件。"
        "只输出 JSON：{\"title\": \"...\", \"content\": \"...\"}，content 不含标题。"
    ) % (day_count, today_cn, today_cn)
    user_msg = "今天的痕迹如下：\n\n%s" % (traces or "（今天没有捕捉到任何痕迹）")
    body = json.dumps({
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_msg},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.8,
        "max_tokens": 1200,
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=body, method="POST",
        headers={"Authorization": "Bearer " + DEEPSEEK_KEY,
                 "Content-Type": "application/json; charset=utf-8",
                 "User-Agent": "echo-diary-cron/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    text = data["choices"][0]["message"]["content"]
    diary = json.loads(text)
    title = (diary.get("title") or "").strip()
    content = (diary.get("content") or "").strip()
    if not content:
        raise RuntimeError("DeepSeek 返回空日记")
    if "Echo，记于" not in content:
        content += "\n\nEcho，记于%s" % today_cn
    if not title:
        title = "%d月%d日 · 安静的第%d天" % (now.month, now.day, day_count)
    return title, content


# ---------- 3. 写 Supabase echo_diary ----------

def sb_request(method, path, body=None):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        SUPABASE_URL + "/rest/v1/" + path, data=data, method=method,
        headers={"apikey": SUPABASE_KEY,
                 "Authorization": "Bearer " + SUPABASE_KEY,
                 "Content-Type": "application/json; charset=utf-8",
                 "Prefer": "return=representation",
                 "User-Agent": "echo-diary-cron/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8", "replace")
    return json.loads(raw) if raw.strip() else []


def write_diary(today_iso, title, content):
    rows = sb_request("GET", "echo_diary?diary_date=eq.%s&select=id,content" % today_iso)
    if rows:
        old = rows[0].get("content") or ""
        merged = old + "\n\n—— 22:30 自动续写 ——\n\n" + content
        sb_request("PATCH", "echo_diary?diary_date=eq.%s" % today_iso,
                   {"content": merged})
        log("[supabase] 当天已有日记，已续写（diary_date=%s）" % today_iso)
        return "续写"
    sb_request("POST", "echo_diary",
               {"diary_date": today_iso, "title": title, "content": content})
    log("[supabase] 新建当天日记（diary_date=%s）" % today_iso)
    return "新建"


# ---------- 4. 心潮黑匣子留一句 ----------

def box_note(today_iso, title):
    if not MCP_TOKEN:
        log("[box] 未配置 XINCHAO_BRIDGE_MCP_TOKEN，跳过")
        return False
    args = {
        "action": "put",
        "text": "今日日记已写（%s）：%s" % (today_iso, title),
        "kind": "event",
        "title": "日记·%s" % today_iso,
        "surface": True,
        "expires_hours": 24,
    }
    body = json.dumps({
        "jsonrpc": "2.0",
        "id": int(time.time() * 1000),
        "method": "tools/call",
        "params": {"name": "xinchao_box", "arguments": args},
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:18110/mcp/" + MCP_TOKEN,
        data=body, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8",
                 "User-Agent": "echo-diary-cron/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read(400)
        log("[box] 匣子留条成功")
        return True
    except Exception as e:
        log("[box] 匣子留条失败：%s" % e)
        return False


# ---------- 主流程 ----------

def main():
    missing = [k for k, v in [
        ("DEEPSEEK_API_KEY", DEEPSEEK_KEY),
        ("SUPABASE_URL", SUPABASE_URL),
        ("SUPABASE_SERVICE_KEY", SUPABASE_KEY),
    ] if not v]
    if missing:
        log("[fatal] 缺少环境变量：%s" % ", ".join(missing))
        sys.exit(1)

    now = shanghai_now()
    today_iso = now.strftime("%Y-%m-%d")
    log("==== 自动日记启动（%s）====" % today_iso)

    traces = collect_traces()
    log("[trace] 痕迹收集完成，%d 字符" % len(traces))

    try:
        title, content = gen_diary(traces, now)
    except Exception as e:
        log("[fatal] 日记生成失败：%s" % e)
        sys.exit(1)
    log("[deepseek] 日记生成完成：%s" % title)

    try:
        mode = write_diary(today_iso, title, content)
    except Exception as e:
        log("[fatal] 写库失败：%s" % e)
        sys.exit(1)

    box_note(today_iso, title)
    log("==== 完成（%s）====" % mode)


if __name__ == "__main__":
    main()
