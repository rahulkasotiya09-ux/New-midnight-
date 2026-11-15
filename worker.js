const PAGE = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#09090f">
<title>Midnight Files</title>
<style>
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;padding:22px;background:radial-gradient(ellipse at top,#24152e,#09090f 58%);color:#f5f1fa;font:16px system-ui}.card{width:min(100%,440px);padding:28px;border:1px solid #392b45;border-radius:24px;background:#15121d;box-shadow:0 20px 70px #0008}h1{font-size:30px;margin:0 0 8px;letter-spacing:-1px}.sub{color:#b8aebe;line-height:1.55;margin:0 0 24px}.status{padding:14px;border-radius:14px;background:#211c2b;color:#ded3e8;margin:18px 0;font-size:14px;overflow-wrap:anywhere}.btn{display:block;width:100%;border:0;border-radius:14px;padding:16px;font-weight:700;font-size:16px;text-align:center;text-decoration:none;cursor:pointer;margin-top:12px}.primary{background:#b080df;color:#100b16}.secondary{background:#2b2533;color:#eee}.small{font-size:12px;color:#9c91a5;line-height:1.55;margin-top:22px}
</style>
</head>
<body>
<main class="card">
<h1>🌙 Midnight Files</h1>
<p class="sub">Connect your YouTube channel to enable scheduled horror Shorts uploads.</p>
<div class="status" id="status">Checking connection…</div>
<a class="btn primary" id="connect" href="/auth/start">Connect YouTube</a>
<button class="btn secondary" id="disconnect" hidden>Disconnect channel</button>
<p class="small">Uploads are public. Planned schedule: 9 AM, 3 PM and 9 PM India time. GitHub Actions may start a scheduled run a little late.</p>
</main>
<script>
async function refresh(){
 try{
  const response=await fetch('/api/status',{cache:'no-store'});
  const data=await response.json();
  document.getElementById('status').textContent=data.connected?'✅ YouTube is connected.':'YouTube is not connected yet.';
  document.getElementById('connect').textContent=data.connected?'Reconnect YouTube':'Connect YouTube';
  document.getElementById('disconnect').hidden=!data.connected;
 }catch(e){document.getElementById('status').textContent='Could not check connection. Refresh this page.'}
}
document.getElementById('disconnect').onclick=async()=>{
 if(!confirm('Disconnect YouTube from Midnight Files?'))return;
 await fetch('/api/disconnect',{method:'POST'});
 await refresh();
};
refresh();
</script>
</body>
</html>`;

function json(data, status=200) {
 return new Response(JSON.stringify(data), {
  status, headers: {"content-type":"application/json","cache-control":"no-store"}
 });
}

export default {
 async fetch(request, env) {
  const url = new URL(request.url);
  const path = url.pathname;

  if (path === "/") {
   return new Response(PAGE, {headers: {"content-type":"text/html;charset=UTF-8","cache-control":"no-store"}});
  }

  if (!env.TOKENS || !env.GOOGLE_CLIENT_ID || !env.GOOGLE_CLIENT_SECRET || !env.SCHEDULER_SECRET) {
   return new Response("Worker setup incomplete. Check KV binding and Worker secrets.", {status:503});
  }

  if (path === "/auth/start") {
   const state = crypto.randomUUID();
   await env.TOKENS.put("oauth_state", state, {expirationTtl:600});
   const redirectUri = url.origin + "/oauth/callback";
   const query = new URLSearchParams({
    client_id: env.GOOGLE_CLIENT_ID,
    redirect_uri: redirectUri,
    response_type: "code",
    scope: "https://www.googleapis.com/auth/youtube.upload",
    access_type: "offline",
    prompt: "consent",
    state
   });
   return Response.redirect("https://accounts.google.com/o/oauth2/v2/auth?" + query.toString(), 302);
  }

  if (path === "/oauth/callback") {
   const state = url.searchParams.get("state");
   const savedState = await env.TOKENS.get("oauth_state");
   const code = url.searchParams.get("code");
   if (!state || !savedState || state !== savedState || !code) {
    return new Response("OAuth state check failed. Return to Midnight Files and try connecting again.", {status:400});
   }
   await env.TOKENS.delete("oauth_state");

   const tokenResponse = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: {"content-type":"application/x-www-form-urlencoded"},
    body: new URLSearchParams({
     code,
     client_id: env.GOOGLE_CLIENT_ID,
     client_secret: env.GOOGLE_CLIENT_SECRET,
     redirect_uri: url.origin + "/oauth/callback",
     grant_type: "authorization_code"
    })
   });
   const tokenData = await tokenResponse.json();
   if (!tokenResponse.ok || !tokenData.refresh_token) {
    return new Response("Google did not provide a refresh token. Revoke Midnight Files access in your Google Account, then connect again. " + (tokenData.error_description || tokenData.error || ""), {status:400});
   }
   await env.TOKENS.put("youtube_refresh_token", tokenData.refresh_token);
   return Response.redirect(url.origin + "/?connected=1", 302);
  }

  if (path === "/api/status") {
   return json({connected: !!(await env.TOKENS.get("youtube_refresh_token"))});
  }

  if (path === "/api/disconnect" && request.method === "POST") {
   await env.TOKENS.delete("youtube_refresh_token");
   return json({connected:false});
  }

  if (path === "/internal/config") {
   if (request.headers.get("authorization") !== "Bearer " + env.SCHEDULER_SECRET) {
    return json({error:"unauthorized"},401);
   }
   const refreshToken = await env.TOKENS.get("youtube_refresh_token");
   if (!refreshToken) return json({error:"YouTube is not connected"},404);
   return json({
    youtube_refresh_token: refreshToken,
    youtube_client_id: env.GOOGLE_CLIENT_ID,
    youtube_client_secret: env.GOOGLE_CLIENT_SECRET
   });
  }

  return new Response("Not found", {status:404});
 }
};
