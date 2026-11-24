const express = require('express');
const fs = require('fs');
const path = require('path');
const { Client } = require('discord.js-selfbot-v13');

const app = express();
app.use(express.json());

const CONFIG_PATH = path.join(__dirname, 'config.json');
const PORT = process.env.PORT || 3000;

let config = {
    enabled: true,
    guildId: process.env.GUILD_ID || '',
    channelId: process.env.CHANNEL_ID || '',
    selfMute: true,
    selfDeaf: false
};

if (fs.existsSync(CONFIG_PATH)) {
    try {
        config = { ...config, ...JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8')) };
    } catch {}
}

function saveConfig() {
    fs.writeFileSync(CONFIG_PATH, JSON.stringify(config, null, 2));
}

const logs = [];
function log(msg) {
    const t = new Date().toLocaleTimeString('ar-EG', { hour12: false });
    const line = `[${t}] ${msg}`;
    logs.unshift(line);
    if (logs.length > 50) logs.pop();
    console.log(line);
}

const streamer = new Client({ checkUpdate: false });
const watcherToken = (process.env.WATCHER_TOKEN || '').trim();
const watcher = watcherToken ? new Client({ checkUpdate: false }) : null;

let streamerReady = false;
let watcherReady = false;
let isStreaming = false;
let isWatching = false;

function sendVoice(client, guildId, channelId, mute = true, deaf = false) {
    if (!client?.ws) return;
    client.ws.broadcast({
        op: 4,
        d: {
            guild_id: guildId,
            channel_id: channelId,
            self_mute: mute,
            self_deaf: deaf,
            self_video: false
        }
    });
}

function startStream(client, guildId, channelId) {
    if (!client?.ws) return;
    client.ws.broadcast({
        op: 18,
        d: {
            type: 'guild',
            guild_id: guildId,
            channel_id: channelId,
            preferred_region: null
        }
    });
    isStreaming = true;
}

function watchStream(client, guildId, channelId, streamerId) {
    if (!client?.ws || !streamerId) return;
    client.ws.broadcast({
        op: 18,
        d: {
            type: 4,
            guild_id: guildId,
            channel_id: channelId,
            stream_key: `stream:${guildId}:${channelId}:${streamerId}`,
            preferred_region: null
        }
    });
    isWatching = true;
}

function stopAll() {
    if (config.guildId) {
        sendVoice(streamer, config.guildId, null, true, false);
        if (watcher && watcherReady) sendVoice(watcher, config.guildId, null, true, false);
    }
    isStreaming = false;
    isWatching = false;
    log('تم الإيقاف والخروج.');
}

function runAll() {
    if (!config.enabled) return stopAll();
    if (!streamerReady || !config.guildId || !config.channelId) return;

    isStreaming = false;
    isWatching = false;

    sendVoice(streamer, config.guildId, config.channelId, config.selfMute, config.selfDeaf);
    log('الأول دخل الروم.');

    setTimeout(() => {
        if (!config.enabled || !streamerReady) return;
        startStream(streamer, config.guildId, config.channelId);
        log('الأول فتح الشير.');
    }, 1500);

    if (watcher && watcherReady && streamer.user) {
        setTimeout(() => {
            if (!config.enabled || !watcherReady) return;
            sendVoice(watcher, config.guildId, config.channelId, true, false);
            log('الثاني دخل الروم.');
        }, 2500);

        setTimeout(() => {
            if (!config.enabled || !watcherReady || !streamer.user) return;
            watchStream(watcher, config.guildId, config.channelId, streamer.user.id);
            log('الثاني بدأ المشاهدة.');
        }, 4000);
    }
}

streamer.on('ready', () => {
    streamerReady = true;
    log(`دخل حساب البث: ${streamer.user.tag}`);
    runAll();
});

if (watcher) {
    watcher.on('ready', () => {
        watcherReady = true;
        log(`دخل حساب المشاهد: ${watcher.user.tag}`);
        if (config.enabled && streamerReady && config.guildId && config.channelId) {
            runAll();
        }
    });
}

app.get('/api/status', (req, res) => {
    res.json({
        streamerReady,
        streamerTag: streamer.user?.tag || 'غير متصل',
        watcherEnabled: Boolean(watcher),
        watcherReady,
        watcherTag: watcher?.user?.tag || 'غير مفعل',
        isStreaming,
        isWatching,
        config,
        logs
    });
});

app.post('/api/update', (req, res) => {
    const oldGuild = config.guildId;
    if (typeof req.body.enabled === 'boolean') config.enabled = req.body.enabled;
    if (typeof req.body.guildId === 'string') config.guildId = req.body.guildId.trim();
    if (typeof req.body.channelId === 'string') config.channelId = req.body.channelId.trim();
    if (typeof req.body.selfMute === 'boolean') config.selfMute = req.body.selfMute;
    if (typeof req.body.selfDeaf === 'boolean') config.selfDeaf = req.body.selfDeaf;
    saveConfig();

    if (oldGuild && oldGuild !== config.guildId) {
        sendVoice(streamer, oldGuild, null, true, false);
        if (watcher && watcherReady) sendVoice(watcher, oldGuild, null, true, false);
    }

    runAll();
    res.json({ ok: true, config });
});

app.get('/', (req, res) => {
    res.type('html').send(`<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>تحكم البث والمشاهدة</title>
<style>
  body { font-family: sans-serif; background: #111827; color: #f9fafb; padding: 20px; margin: 0; }
  .box { max-width: 650px; margin: auto; background: #1f2937; padding: 20px; border-radius: 12px; }
  input { width: 100%; padding: 10px; margin: 6px 0 14px; border-radius: 8px; border: 1px solid #374151; background: #111827; color: #fff; box-sizing: border-box; }
  button { padding: 10px 16px; border: none; border-radius: 8px; cursor: pointer; font-weight: bold; margin-left: 8px; }
  .save { background: #2563eb; color: #fff; }
  .toggle { background: #dc2626; color: #fff; }
  .on { background: #16a34a; color: #fff; }
  .status { margin-bottom: 14px; font-size: 14px; line-height: 1.8; }
  .logs { background: #090d16; padding: 12px; border-radius: 8px; height: 180px; overflow-y: auto; font-family: monospace; font-size: 12px; margin-top: 14px; direction: ltr; text-align: left; }
</style>
</head>
<body>
<div class="box">
  <h2>تحكم الشير والمشاهدة</h2>
  <div class="status" id="status">جاري التحميل...</div>
  <label>ايدي السيرفر (Guild ID)</label>
  <input id="guildId" type="text" />
  <label>ايدي الروم (Channel ID)</label>
  <input id="channelId" type="text" />
  <label><input id="selfMute" type="checkbox" /> كتم مايك الأول</label>
  <label style="margin-right:16px"><input id="selfDeaf" type="checkbox" /> كتم سماعة الأول</label>
  <div style="margin-top:14px">
    <button class="save" onclick="save()">حفظ وتطبيق</button>
    <button id="btnToggle" class="toggle" onclick="toggle()">إيقاف</button>
  </div>
  <div class="logs" id="logs"></div>
</div>
<script>
let loaded = false;
let enabled = true;

async function load() {
  const r = await fetch('/api/status');
  const d = await r.json();
  enabled = d.config.enabled;
  document.getElementById('status').innerHTML =
    'الأول: ' + d.streamerTag + ' | شير: ' + (d.isStreaming ? 'شغال' : 'طافي') + '<br>' +
    'الثاني: ' + d.watcherTag + ' | مشاهدة: ' + (d.isWatching ? 'يشاهد' : 'لا') + '<br>' +
    'السكربت: ' + (enabled ? 'شغال' : 'طافي');

  const btn = document.getElementById('btnToggle');
  btn.textContent = enabled ? 'إيقاف' : 'تشغيل';
  btn.className = enabled ? 'toggle' : 'on';

  if (!loaded) {
    document.getElementById('guildId').value = d.config.guildId || '';
    document.getElementById('channelId').value = d.config.channelId || '';
    document.getElementById('selfMute').checked = Boolean(d.config.selfMute);
    document.getElementById('selfDeaf').checked = Boolean(d.config.selfDeaf);
    loaded = true;
  }
  document.getElementById('logs').innerHTML = (d.logs || []).map(x => '<div>' + x + '</div>').join('');
}

async function save() {
  await fetch('/api/update', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      guildId: document.getElementById('guildId').value.trim(),
      channelId: document.getElementById('channelId').value.trim(),
      selfMute: document.getElementById('selfMute').checked,
      selfDeaf: document.getElementById('selfDeaf').checked
    })
  });
  loaded = false;
  load();
}

async function toggle() {
  await fetch('/api/update', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled: !enabled })
  });
  load();
}

load();
setInterval(load, 3000);
</script>
</body>
</html>`);
});

app.listen(PORT, () => log(`شغال على المنفذ ${PORT}`));

if (process.env.DISCORD_TOKEN) {
    streamer.login(process.env.DISCORD_TOKEN.trim()).catch(e => log('خطأ دخول الأول: ' + e.message));
} else {
    log('حط DISCORD_TOKEN بمتغيرات الاستضافة.');
}

if (watcher && watcherToken) {
    watcher.login(watcherToken).catch(e => log('خطأ دخول الثاني: ' + e.message));
}
