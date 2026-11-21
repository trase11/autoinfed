// Cloudflare Worker για το Telegram bot του autoinfed.
//
// Τι κάνει:
//  • Δέχεται αμέσως κάθε μήνυμα/πάτημα κουμπιού από το Telegram (webhook)
//    και απαντάει επιτόπου («✓ Πήρα τον τίτλο», «📷 Πήρα τις φωτογραφίες»…).
//  • Κρατάει τα μηνύματα σε ουρά (KV) για να τα διαβάσει το GitHub Actions.
//  • Όταν χρειάζεται δουλειά (✅ Τέλος, ✅/❌ σε προεπισκόπηση, αλλαγή με reply)
//    ξεκινάει αμέσως το workflow στο GitHub (~1 λεπτό αντί για 15).
//
// Ρυθμίσεις στο Cloudflare (Settings → Variables and Secrets / Bindings):
//   BOT_TOKEN  (secret)  το token του bot από τον BotFather
//   GH_TOKEN   (secret)  fine-grained token GitHub με «Actions: Read and write» στο autoinfed
//   CHAT_ID    (text)    6721979664
//   GH_REPO    (text)    trase11/autoinfed
//   Q          (KV namespace binding)
//   Cron Trigger (Settings → Triggers): */5 * * * *  → έλεγχος των site κάθε 5 λεπτά,
//                 γιατί το χρονοδιάγραμμα του GitHub χάνει τις περισσότερες εκτελέσεις.
//
// Μετά το deploy άνοιξε μία φορά:  https://<worker>.workers.dev/setup

const TTL = 3 * 24 * 3600;          // τα μηνύματα κρατιούνται 3 μέρες στην ουρά
const NEW = "📝 Νέα: ";
const OWN_COVER = "🖼 Δικό μου εξώφυλλο";
const DONE = "✅ Τέλος";
const CANCEL = "🗑 Ακύρωση";
const MAX_SLIDES = 10;

async function sha256hex(s) {
  const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return [...new Uint8Array(d)].map(b => b.toString(16).padStart(2, "0")).join("");
}
const pad = n => String(n).padStart(15, "0");

async function tg(env, method, body) {
  const r = await fetch(`https://api.telegram.org/bot${env.BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  return r.json();
}
const say = (env, text) => tg(env, "sendMessage", { chat_id: env.CHAT_ID, text });

async function dispatch(env, updateId, quiet = false) {
  const r = await fetch(
    `https://api.github.com/repos/${env.GH_REPO}/actions/workflows/announce.yml/dispatches`, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${env.GH_TOKEN}`,
        "Accept": "application/vnd.github+json",
        "User-Agent": "autoinfed-worker",
        "content-type": "application/json",
      },
      body: JSON.stringify({ ref: "main", inputs: { wait_for: String(updateId) } }),
    });
  if (r.status >= 300 && !quiet) {
    await say(env, `⚠️ Δεν ξεκίνησε η δουλειά στο GitHub (${r.status}). ` +
                   "Θα γίνει στον επόμενο αυτόματο έλεγχο (έως 15').");
  }
}

// Κάθε άτομο έχει το δικό του πρόχειρο (για ομάδες με πολλούς διαχειριστές)
async function getStage(env, uid) {
  return JSON.parse((await env.Q.get("stage:" + uid)) || '{"active":false}');
}
const setStage = (env, uid, s) => env.Q.put("stage:" + uid, JSON.stringify(s), { expirationTtl: 7 * 24 * 3600 });

async function handle(u, env) {
  // 1) πάντα στην ουρά, για το GitHub
  await env.Q.put("u:" + pad(u.update_id), JSON.stringify(u), { expirationTtl: TTL });

  // 2) άμεση απάντηση μόνο στη δική σου συνομιλία
  const cq = u.callback_query;
  if (cq) {
    if (String(cq.message?.chat?.id) !== String(env.CHAT_ID)) return;
    const yes = (cq.data || "").startsWith("y:");
    await tg(env, "answerCallbackQuery", {
      callback_query_id: cq.id,
      text: yes ? "✅ Ανεβαίνει στο Instagram σε ~1 λεπτό" : "❌ Δεν θα ανέβει",
    });
    await dispatch(env, u.update_id);
    return;
  }
  const m = u.message;
  if (!m || String(m.chat?.id) !== String(env.CHAT_ID)) return;

  const text = (m.text || "").trim().replace(/@\w+bot$/i, "");   // «/start@onoma_bot» σε ομάδες
  const isPhoto = !!(m.photo || (m.document && (m.document.mime_type || "").startsWith("image/")));
  const uid = String(m.from?.id || "me");
  const who = m.from?.first_name ? m.from.first_name + ": " : "";
  const isGroup = m.chat?.type === "group" || m.chat?.type === "supergroup";
  const toBot = !!m.reply_to_message?.from?.is_bot;
  const st = await getStage(env, uid);
  const sayTo = t => say(env, isGroup ? who + t : t);

  if (["/start", "/menu", "/help", "βοήθεια"].includes(text)) {
    await sayTo("👋 Σου στέλνω τα κουμπιά σε λίγα δευτερόλεπτα…");
    await dispatch(env, u.update_id);
  } else if (text.startsWith(NEW)) {
    if (st.active && (st.title || st.photos)) await sayTo("🗑 Το προηγούμενο πρόχειρο πετάχτηκε.");
    await setStage(env, uid, { active: true, title: false, photos: 0 });
    await sayTo(`📝 Νέο πρόχειρο για ${text.slice(NEW.length)}.\n\nΓράψε τώρα τον τίτλο.`);
  } else if (text === CANCEL) {
    await setStage(env, uid, { active: false });
    await sayTo(st.active ? "🗑 Το πρόχειρο ακυρώθηκε." : "Δεν υπάρχει πρόχειρο για ακύρωση.");
  } else if (text === OWN_COVER) {
    await sayTo(st.active ? "✓ Η 1η φωτογραφία θα γίνει το εξώφυλλο."
                             : "Πάτα πρώτα «📝 Νέα: …» για τον σύλλογο.");
  } else if (text === DONE) {
    if (!st.active) {
      await sayTo("Δεν υπάρχει πρόχειρο. Πάτα «📝 Νέα: …» για να ξεκινήσεις.");
    } else if (!st.title) {
      await sayTo("⚠️ Λείπει ο τίτλος. Γράψε τον τίτλο και πάτα ξανά «✅ Τέλος».");
    } else {
      await setStage(env, uid, { active: false });
      await sayTo("⏳ Φτιάχνω την προεπισκόπηση… (περίπου 1 λεπτό)");
      await dispatch(env, u.update_id);
    }
  } else if (toBot && text) {
    await sayTo("✏️ Πήρα την αλλαγή, φτιάχνω νέα προεπισκόπηση… (περίπου 1 λεπτό)");
    await dispatch(env, u.update_id);
  } else if (isPhoto) {
    if (!st.active) {
      if (!isGroup) await sayTo("Πάτα πρώτα «📝 Νέα: …» για τον σύλλογο και μετά στείλε φωτογραφίες.");
      return;
    }
    st.photos = (st.photos || 0) + 1;
    if (m.caption && !st.title) st.title = true;
    await setStage(env, uid, st);
    if (st.photos > MAX_SLIDES) {
      await sayTo(`⚠️ Το καρουσέλ χωράει έως ${MAX_SLIDES} εικόνες· οι επιπλέον αγνοούνται.`);
    } else if (m.media_group_id) {
      const k = "mg:" + m.media_group_id;           // μία απάντηση ανά άλμπουμ
      if (!(await env.Q.get(k))) {
        await env.Q.put(k, "1", { expirationTtl: 3600 });
        await sayTo("📷 Πήρα τις φωτογραφίες (με αυτή τη σειρά στο καρουσέλ).\n" +
                       "Στείλε κι άλλες, γράψε λεζάντα ή πάτα «✅ Τέλος».");
      }
    } else {
      await sayTo(`📷 Φωτογραφία ${st.photos}. Στείλε κι άλλες, γράψε λεζάντα ή πάτα «✅ Τέλος».`);
    }
  } else if (text) {
    if (!st.active) {
      if (isGroup) return;                     // κανονική κουβέντα στην ομάδα: σιωπή
      await sayTo("Για νέα ανακοίνωση πάτα «📝 Νέα: …».\n" +
                     "Για αλλαγή σε προεπισκόπηση κάνε «Απάντηση» (reply) στο μήνυμα με την εικόνα.");
    } else if (!st.title) {
      st.title = true;
      await setStage(env, uid, st);
      await sayTo(`✓ Τίτλος: «${text}»\n\nΤώρα στείλε φωτογραφίες (με τη σειρά του καρουσέλ) ` +
                     "και/ή γράψε τη λεζάντα. Στο τέλος πάτα «✅ Τέλος».");
    } else {
      await sayTo("✓ Λεζάντα αποθηκεύτηκε. Πάτα «✅ Τέλος» όταν είσαι έτοιμος.");
    }
  }
}

export default {
  // Αξιόπιστο «ξυπνητήρι»: ξεκινάει τον έλεγχο των site στο GitHub κάθε 5 λεπτά
  async scheduled(event, env, ctx) {
    ctx.waitUntil(dispatch(env, "", true));
  },

  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    const hookSecret = (await sha256hex("autoinfed-webhook:" + env.BOT_TOKEN)).slice(0, 64);

    if (url.pathname === "/setup") {
      const r = await tg(env, "setWebhook", {
        url: `${url.origin}/tg`,
        secret_token: hookSecret,
        allowed_updates: ["message", "callback_query"],
      });
      return new Response(r.ok ? "✅ Το bot συνδέθηκε με το Cloudflare. Μπορείς να κλείσεις τη σελίδα."
                               : `❌ ${r.description}`,
                          { headers: { "content-type": "text/plain; charset=utf-8" } });
    }

    if (url.pathname === "/tg" && req.method === "POST") {
      if (req.headers.get("X-Telegram-Bot-Api-Secret-Token") !== hookSecret) {
        return new Response("forbidden", { status: 403 });
      }
      const u = await req.json();
      ctx.waitUntil(handle(u, env).catch(e => console.log("handle error", e)));
      return new Response("ok");
    }

    if (url.pathname === "/updates") {
      // Το GitHub Actions διαβάζει από εδώ τα μηνύματα (αντί για getUpdates)
      const key = await sha256hex("autoinfed-queue:" + env.BOT_TOKEN);
      if (req.headers.get("Authorization") !== `Bearer ${key}`) {
        return new Response("forbidden", { status: 403 });
      }
      const after = Number(url.searchParams.get("after") || 0);
      const out = [];
      let cursor;
      do {
        const page = await env.Q.list({ prefix: "u:", cursor });
        for (const k of page.keys) {
          if (Number(k.name.slice(2)) > after) {
            const v = await env.Q.get(k.name);
            if (v) out.push(JSON.parse(v));
          }
        }
        cursor = page.list_complete ? null : page.cursor;
      } while (cursor);
      out.sort((a, b) => a.update_id - b.update_id);
      return Response.json(out);
    }

    return new Response("autoinfed bot ✓", { headers: { "content-type": "text/plain; charset=utf-8" } });
  },
};
