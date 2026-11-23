const { Client } = require('discord.js-selfbot-v13');
const { joinVoiceChannel } = require('@discordjs/voice');

// ================= المتغيرات المطلوب تعبئتها =================
const CONFIG = {
    TOKEN: "ضع_توكن_الحساب_هنا",
    GUILD_ID: "ضع_ايدي_السيرفر_هنا",
    CHANNEL_ID: "ضع_ايدي_الروم_الصوتي_هنا",
    SELF_MUTE: true,   // true = كتم المايك | false = فتح المايك
    SELF_DEAF: false   // true = كتم السماعة | false = فتح السماعة
};
// =============================================================

const client = new Client({
    checkUpdate: false
});

async function connectAndStream() {
    try {
        const guild = client.guilds.cache.get(CONFIG.GUILD_ID);
        if (!guild) return console.log("❌ لم يتم العثور على السيرفر، تأكد من ID السيرفر.");

        const channel = guild.channels.cache.get(CONFIG.CHANNEL_ID);
        if (!channel || channel.type !== 'GUILD_VOICE') {
            return console.log("❌ لم يتم العثور على الروم الصوتي، تأكد من ID الروم.");
        }

        // 1. الدخول للروم الصوتي (AFK)
        const connection = joinVoiceChannel({
            channelId: channel.id,
            guildId: guild.id,
            adapterCreator: guild.voiceAdapterCreator,
            selfMute: CONFIG.SELF_MUTE,
            selfDeaf: CONFIG.SELF_DEAF
        });

        console.log(`✅ تم الدخول إلى الروم: ${channel.name}`);

        // 2. إرسال حزمة تفعيل السكرين شير (Go Live)
        setTimeout(() => {
            client.ws.broadcast({
                op: 18, // OP Code الخاص بإنشاء بث Go Live
                d: {
                    type: "guild",
                    guild_id: guild.id,
                    channel_id: channel.id,
                    preferred_region: null
                }
            });
            console.log("🖥️ تم تفعيل وضع السكرين شير (Go Live)");
        }, 3000);

    } catch (error) {
        console.error("⚠️ حدث خطأ:", error);
    }
}

client.on('ready', () => {
    console.log(`🚀 تم تسجيل الدخول باسم: ${client.user.tag}`);
    connectAndStream();
});

// إعادة الاتصال تلقائياً في حال تم فصل الحساب أو سحبه من الروم
client.on('voiceStateUpdate', (oldState, newState) => {
    if (oldState.member?.id === client.user.id && !newState.channelId) {
        console.log("🔄 تم فصل الحساب من الروم، جاري إعادة الدخول خلال 5 ثوانٍ...");
        setTimeout(connectAndStream, 5000);
    }
});

client.login(CONFIG.TOKEN);
