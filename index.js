const express = require('express');
const fs = require('fs');
const path = require('path');
const { Client } = require('discord.js-selfbot-v13');
const { joinVoiceChannel, getVoiceConnection } = require('@discordjs/voice');

const app = express();
app.use(express.json());

const CONFIG_PATH = path.join(__dirname, 'config.json');
const PORT = process.env.PORT || 3000;

const defaultConfig = {
    enabled: true,
    guildId: process.env.GUILD_ID || '',
    channelId: process.env.CHANNEL_ID || '',
    selfMute: process.env.SELF_MUTE !== 'false',
    selfDeaf: process.env.SELF_DEAF === 'true'
};

function loadConfig() {
    try {
        if (fs.existsSync(CONFIG_PATH)) {
            const raw = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'));
            return { ...defaultConfig, ...raw };
        }
    } catch (err) {
        addLog(`⚠️ خطأ في قراءة config.json: ${err.message}`);
    }
    return { ...defaultConfig };
}

function saveConfig(newCfg) {
    const merged = {
        enabled: Boolean(newCfg.enabled),
        guildId: String(newCfg.guildId || '').trim(),
        channelId: String(newCfg.channelId || '').trim(),
        selfMute: Boolean(newCfg.selfMute),
        selfDeaf: Boolean(newCfg.selfDeaf)
    };
    fs.writeFileSync(CONFIG_PATH, JSON.stringify(merged, null, 2), 'utf8');
    return merged;
}

let config = saveConfig(loadConfig());
const logs = [];
let botReady = false;
let botUserTag = 'غير متصل';
let streamActive = false;

function addLog(message) {
    const time = new Date().toLocaleTimeString('ar-EG', { hour12: false });
    const entry = `[${time}] ${message}`;
    logs.unshift(entry);
    if (logs.length > 60) logs.pop();
    console.log(entry);
}

const client = new Client({
    checkUpdate: false
});

function destroyCurrentVoice(reason = '') {
    if (config.guildId) {
        const existing = getVoiceConnection(config.guildId);
        if (existing) {
            existing.destroy();
        }
    }
    if (client.guilds && client.guilds.cache) {
        client.guilds.cache.forEach((g) => {
            const conn = getVoiceConnection(g.id);
            if (conn) conn.destroy();
        });
    }
    streamActive = false;
    if (reason) addLog(reason);
}

async function applyVoiceState() {
    if (!botReady) {
        addLog('⏳ الحساب لم يكتمل تسجيل دخوله بعد...');
        return;
    }

    if (!config.enabled) {
        destroyCurrentVoice('🛑 تم إيقاف السكربت والخروج من الروم الصوتي.');
        return;
    }

    if (!config.guildId || !config.channelId) {
        addLog('⚠️ يرجى إدخال GUILD_ID و CHANNEL_ID من لوحة التحكم.');
        return;
    }

    const guild = client.guilds.cache.get(config.guildId);
    if (!guild) {
        streamActive = false;
        addLog(`❌ لم يتم العثور على السيرفر (${config.guildId}).`);
        return;
    }

    const channel = guild.channels.cache.get(config.channelId);
    if (!channel || (channel.type !== 'GUILD_VOICE' && channel.type !== 'GUILD_STAGE_VOICE')) {
        streamActive = false;
        addLog(`❌ الروم الصوتي غير موجود أو ليس روم صوتي (${config.channelId}).`);
        return;
    }

    try {
        destroyCurrentVoice();

        joinVoiceChannel({
            channelId: channel.id,
            guildId: guild.id,
            adapterCreator: guild.voiceAdapterCreator,
            selfMute: config.selfMute,
            selfDeaf: config.selfDeaf
        });

        addLog(`✅ تم الدخول إلى الروم: ${channel.name} (${guild.name})`);

        setTimeout(() => {
            if (!config.enabled || !botReady) return;
            try {
                client.ws.broadcast({
                    op: 18,
                    d: {
                        type: 'guild',
                        guild_id: guild.id,
                        channel_id: channel.id,
                        preferred_region: null
                    }
                });
                streamActive = true;
                addLog(`🖥️ تم تفعيل السكرين شير (Go Live) في الروم: ${channel.name}`);
            } catch (wsErr) {
                addLog(`⚠️ تعذر إرسال حزمة Go Live: ${wsErr.message}`);
            }
        }, 2500);
    } catch (err) {
        streamActive = false;
        addLog(`⚠️ خطأ أثناء الاتصال بالروم: ${err.message}`);
    }
}

client.on('ready', () => {
    botReady = true;
    botUserTag = client.user ? client.user.tag : 'متصل';
    addLog(`🚀 تم تسجيل الدخول باسم: ${botUserTag}`);
    applyVoiceState();
});

client.on('voiceStateUpdate', (oldState, newState) => {
    if (!client.user || oldState.member?.id !== client.user.id) return;

    if (config.enabled && oldState.channelId && !newState.channelId) {
        streamActive = false;
        addLog('🔄 تم فصل الحساب من الروم، جاري إعادة الدخول خلال 5 ثوانٍ...');
        setTimeout(() => {
            if (config.enabled) applyVoiceState();
        }, 5000);
    } else if (config.enabled && newState.channelId && newState.channelId !== config.channelId) {
        addLog(`🔀 تم نقل الحساب إلى روم آخر (${newState.channelId})، جاري إعادته للروم المحدد...`);
        setTimeout(() => {
            if (config.enabled) applyVoiceState();
        }, 2000);
    }
});

// ================= واجهات لوحة التحكم (API) =================
app.get('/api/status', (req, res) => {
    res.json({
        botReady,
        botUserTag,
        streamActive,
        config,
        logs
    });
});

app.post('/api/config', async (req, res) => {
    const previousGuildId = config.guildId;
    config = saveConfig({
        ...config,
        ...req.body
    });

    if (previousGuildId && previousGuildId !== config.guildId) {
        const oldConn = getVoiceConnection(previousGuildId);
        if (oldConn) oldConn.destroy();
    }

    addLog('💾 تم تحديث إعدادات التحكم من اللوحة.');
    await applyVoiceState();
    res.json({ ok: true, config });
});

app.post('/api/toggle', async (req, res) => {
    const nextState = typeof req.body.enabled === 'boolean' ? req.body.enabled : !config.enabled;
    config = saveConfig({ ...config, enabled: nextState });
    addLog(config.enabled ? '▶️ تم تشغيل السكربت من اللوحة.' : '⏹️ تم إيقاف السكربت من اللوحة.');
    await applyVoiceState();
    res.json({ ok: true, config });
});

app.post('/api/reconnect', async (req, res) => {
    addLog('🔁 إعادة الاتصال الفوري بالروم وتجديد البث...');
    await applyVoiceState();
    res.json({ ok: true, config });
});

// ================= صفحة لوحة التحكم =================
app.get('/', (req, res) => {
    res.type('html').send(`<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>لوحة تحكم السيلف بوت (AFK & Go Live)</title>
<style>
  * { box-sizing: border-box; font-family: system-ui, -apple-system, sans-serif; }
  body { margin: 0; background: #0f172a; color: #e2e8f0; padding: 20px; }
  .wrap { max-width: 760px; margin: 0 auto; }
  .card { background: #1e293b; border: 1px solid #334155; border-radius: 14px; padding: 20px; margin-bottom: 16px; }
  h1 { margin: 0 0 6px; font-size: 20px; }
  .sub { color: #94a3b8; font-size: 13px; margin-bottom: 16px; }
  .status-bar { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 16px; }
  .badge { padding: 6px 12px; border-radius: 999px; font-size: 13px; font-weight: 600; background: #0f172a; border: 1px solid #334155; }
  .ok { color: #4ade80; border-color: #166534; }
  .off { color: #f87171; border-color: #991b1b; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  @media (max-width: 600px) { .grid { grid-template-columns: 1fr; } }
  label { display: block; font-size: 13px; color: #cbd5e1; margin-bottom: 6px; }
  input[type="text"] { width: 100%; padding: 10px 12px; border-radius: 10px; border: 1px solid #475569; background: #0f172a; color: #fff; font-size: 14px; }
  .checks { display: flex; gap: 20px; margin: 14px 0; flex-wrap: wrap; }
  .check { display: flex; align-items: center; gap: 8px; font-size: 14px; cursor: pointer; }
  .actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 14px; }
  button { border: none; border-radius: 10px; padding: 10px 16px; font-size: 14px; font-weight: 600; cursor: pointer; transition: 0.15s; }
  .btn-primary { background: #3b82f6; color: #fff; }
  .btn-stop { background: #ef4444; color: #fff; }
  .btn-start { background: #22c55e; color: #052e16; }
  .btn-ghost { background: #334155; color: #e2e8f0; }
  button:hover { opacity: 0.9; }
  .logs { background: #090d16; border: 1px solid #1e293b; border-radius: 10px; padding: 12px; height: 220px; overflow-y: auto; font-family: monospace; font-size: 12px; line-height: 1.6; direction: ltr; text-align: left; }
  .msg { margin-top: 10px; font-size: 13px; color: #38bdf8; min-height: 18px; }
</style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <h1>لوحة تحكم الروم والبث (AFK + Go Live)</h1>
    <div class="sub">تغيير الروم أو السيرفر، تشغيل أو إيقاف السكربت، وتحديث حالة الصوت مباشرة</div>

    <div class="status-bar">
      <div id="botBadge" class="badge">الحساب: ...</div>
      <div id="runBadge" class="badge">السكربت: ...</div>
      <div id="streamBadge" class="badge">البث: ...</div>
    </div>

    <div class="grid">
      <div>
        <label for="guildId">Server ID (ايدي السيرفر)</label>
        <input id="guildId" type="text" placeholder="مثال: 112233445566778899" />
      </div>
      <div>
        <label for="channelId">Voice Channel ID (ايدي الروم الصوتي)</label>
        <input id="channelId" type="text" placeholder="مثال: 998877665544332211" />
      </div>
    </div>

    <div class="checks">
      <label class="check"><input id="selfMute" type="checkbox" /> كتم المايك (Self Mute)</label>
      <label class="check"><input id="selfDeaf" type="checkbox" /> كتم السماعة (Self Deafen)</label>
    </div>

    <div class="actions">
      <button class="btn-primary" onclick="saveSettings()">حفظ وتطبيق</button>
      <button id="toggleBtn" class="btn-stop" onclick="toggleScript()">إيقاف السكربت</button>
      <button class="btn-ghost" onclick="reconnectNow()">إعادة دخول / بث</button>
    </div>
    <div id="feedback" class="msg"></div>
  </div>

  <div class="card">
    <label>سجل العمليات المباشر (Logs)</label>
    <div id="logsBox" class="logs"></div>
  </div>
</div>

<script>
let isInitialized = false;
let currentEnabled = true;

function setMsg(text) {
  document.getElementById('feedback').textContent = text;
}

async function refreshStatus(forceInputs = false) {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();

    const botBadge = document.getElementById('botBadge');
    botBadge.textContent = 'الحساب: ' + (data.botReady ? data.botUserTag : 'جاري الاتصال...');
    botBadge.className = 'badge ' + (data.botReady ? 'ok' : 'off');

    currentEnabled = Boolean(data.config.enabled);
    const runBadge = document.getElementById('runBadge');
    runBadge.textContent = 'السكربت: ' + (currentEnabled ? 'يعمل' : 'متوقف');
    runBadge.className = 'badge ' + (currentEnabled ? 'ok' : 'off');

    const streamBadge = document.getElementById('streamBadge');
    streamBadge.textContent = 'البث: ' + (data.streamActive ? 'LIVE نشط' : 'غير نشط');
    streamBadge.className = 'badge ' + (data.streamActive ? 'ok' : '');

    const toggleBtn = document.getElementById('toggleBtn');
    toggleBtn.textContent = currentEnabled ? 'إيقاف السكربت' : 'تشغيل السكربت';
    toggleBtn.className = currentEnabled ? 'btn-stop' : 'btn-start';

    if (!isInitialized || forceInputs) {
      document.getElementById('guildId').value = data.config.guildId || '';
      document.getElementById('channelId').value = data.config.channelId || '';
      document.getElementById('selfMute').checked = Boolean(data.config.selfMute);
      document.getElementById('selfDeaf').checked = Boolean(data.config.selfDeaf);
      isInitialized = true;
    }

    document.getElementById('logsBox').innerHTML = (data.logs || [])
      .map(line => '<div>' + escapeHtml(line) + '</div>')
      .join('');
  } catch (e) {
    setMsg('تعذر تحديث الحالة حالياً.');
  }
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

async function saveSettings() {
  const payload = {
    guildId: document.getElementById('guildId').value.trim(),
    channelId: document.getElementById('channelId').value.trim(),
    selfMute: document.getElementById('selfMute').checked,
    selfDeaf: document.getElementById('selfDeaf').checked
  };
  const res = await fetch('/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
  if (res.ok) {
    setMsg('تم حفظ الإعدادات وتطبيقها فوراً.');
    await refreshStatus(true);
  }
}

async function toggleScript() {
  const res = await fetch('/api/toggle', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled: !currentEnabled })
  });
  if (res.ok) {
    setMsg(!currentEnabled ? 'تم تشغيل السكربت.' : 'تم إيقاف السكربت.');
    await refreshStatus(false);
  }
}

async function reconnectNow() {
  const res = await fetch('/api/reconnect', { method: 'POST' });
  if (res.ok) {
    setMsg('تم إرسال أمر إعادة الاتصال.');
    await refreshStatus(false);
  }
}

refreshStatus(true);
setInterval(() => refreshStatus(false), 3000);
</script>
</body>
</html>`);
});

app.listen(PORT, () => {
    addLog(`🌐 لوحة التحكم تعمل على المنفذ: ${PORT}`);
});

const token = (process.env.DISCORD_TOKEN || '').trim();
if (!token) {
    addLog('❌ متغير DISCORD_TOKEN غير موجود! أضفه في Environment Variables على الاستضافة.');
} else {
    client.login(token).catch((err) => {
        addLog(`❌ فشل تسجيل الدخول: ${err.message}`);
    });
}
