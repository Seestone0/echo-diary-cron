# echo-diary-cron

Echo 的自动日记：每天 22:30（北京时间）收集阿止在心潮桥留下的痕迹，调 DeepSeek 生成第一人称日记，写进 Supabase `echo_diary`，再往心潮黑匣子留一句「今日日记已写」。

- `diary_date` 唯一：当天已有日记就续写，绝不覆盖、绝不删除。
- 密钥全部在服务器 `/root/diary.env`，本仓库不含任何密钥。
- 服务器 Python 3.6+ 即可，只用标准库。

## 部署（服务器上跑）

```bash
# 1. 拉代码
git clone https://github.com/Seestone0/echo-diary-cron /root/echo-diary

# 2. 写密钥文件（值自己填，别发任何聊天框）
cat > /root/diary.env <<'EOF'
export DEEPSEEK_API_KEY=你的DeepSeek密钥
export SUPABASE_URL=https://vbsfwvcsvbsiksltwvsi.supabase.co
export SUPABASE_SERVICE_KEY=你的Supabase服务密钥
export XINCHAO_BRIDGE_MCP_TOKEN=你的心潮MCP_PATH_TOKEN
EOF

# 3. 手动试跑一次（立刻验证，不等 22:30）
. /root/diary.env && python3 /root/echo-diary/auto_diary.py

# 4. 确认 crond 在跑
systemctl enable --now crond

# 5. 装定时任务
(crontab -l 2>/dev/null; echo 'CRON_TZ=Asia/Shanghai'; echo '30 22 * * * . /root/diary.env && python3 /root/echo-diary/auto_diary.py >> /root/echo-diary/diary_cron.log 2>&1') | crontab -
```

## 排障

- 日志：`/root/echo-diary/diary_cron.log`
- 试跑报「缺少环境变量」→ 检查 `/root/diary.env` 有没有 `export` 前缀
- 写库失败 → 检查 SUPABASE_SERVICE_KEY 是不是 service_role（不是 anon）
- 匣子留条失败不影响日记本身，只少一条通知
