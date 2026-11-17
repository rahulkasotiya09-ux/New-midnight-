
const PAGE = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#09090f">
<title>GTA 5 Shorts Automation</title>
<style>
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:grid;place-items:center;padding:22px;background:radial-gradient(ellipse at top,#24152e,#09090f 58%);color:#f5f1fa;font:16px system-ui}
.card{width:min(100%,440px);padding:28px;border:1px solid #392b45;border-radius:24px;background:#15121d;box-shadow:0 20px 70px #0008}
h1{font-size:27px;margin:0 0 8px}
.sub{color:#b8aebe;line-height:1.55;margin:0 0 24px}
.status{padding:14px;border-radius:14px;background:#211c2b;color:#ded3e8;margin:18px 0;font-size:14px;overflow-wrap:anywhere}
.btn{display:block;width:100%;border:0;border-radius:14px;padding:16px;font-weight:700;font-size:16px;text-align:center;text-decoration:none;cursor:pointer;margin-top:12px}
.primary{background:#b080df;color:#100b16}
.secondary{background:#2b2533;color:#eee}
.small{font-size:12px;color:#9c91a5;line-height:1.55;margin-top:22px}
</style>
</head>
<body>
<main class="card">
<h1>🎮 GTA 5 Shorts Automation</h1>
<p class="sub">Connect Google to allow scheduled Shorts uploads and read-only access to your Google Drive clips.</p>
<div class="status" id="status">Checking connection...</div>
<a class="btn primary" id="connect" href="/auth/start">Connect Google</a>
<p class="small">Google authorization includes YouTube upload and Google Drive read-only permissions. Only upload videos you own or have permission to use.</p>
</main>
<script>
async function refresh(){
 try{
  const response=await fetch('/api/status',{cache:'no-store'});
  const data=await response.json();
  document.getElementById('status').textContent=
   data.connected
   ? '✅ Google account is connected. Reconnect to refresh permissions.'
   : 'Google account is not connected yet.';
  document.getElementById('connect').textContent=
   data.connected ? 'Reconnect Google' : 'Connect Google';
 }catch(e){
  document.getElementById('status').textContent='Could not check connection. Refresh this page.';
 }
}
refresh();
</script>
</body>
</html>`;

const GOOGLE_SCOPES = [
 "https://www.googleapis.com/auth/youtube.upload",
 "https://www.googleapis.com/auth/drive.readonly"
].join(" ");

function json(data, status=200) {
 return new Response(JSON.stringify(data), {
  status,
  headers: {
   "content-type":"application/json",
   "cache-control":"no-store"
  }
 });
}

export default {
 async fetch(request, env) {
  const url = new URL(request.url);
  const path = url.pathname;

  if (path === "/") {
   return new Response(PAGE, {
    headers: {
     "content-type":"text/html;charset=UTF-8",
     "cache-control":"no-store"
    }
   });
  }

  if (
   !env.TOKENS ||
   !env.GOOGLE_CLIENT_ID ||
   !env.GOOGLE_CLIENT_SECRET ||
   !env.SCHEDULER_SECRET
  ) {
   return new Response(
    "Worker setup incomplete. Check KV binding and Worker secrets.",
    {status:503}
   );
  }

  // Start Google OAuth
  if (path === "/auth/start") {
   const state = crypto.randomUUID();

   await env.TOKENS.put("oauth_state", state, {
    expirationTtl:600
   });

   const redirectUri = url.origin + "/oauth/callback";

   const query = new URLSearchParams({
    client_id: env.GOOGLE_CLIENT_ID,
    redirect_uri: redirectUri,
    response_type: "code",
    scope: GOOGLE_SCOPES,
    access_type: "offline",
    prompt: "consent",
    include_granted_scopes: "true",
    state
   });

   return Response.redirect(
    "https://accounts.google.com/o/oauth2/v2/auth?" +
    query.toString(),
    302
   );
  }

  // Google OAuth callback
  if (path === "/oauth/callback") {
   const state = url.searchParams.get("state");
   const savedState = await env.TOKENS.get("oauth_state");
   const code = url.searchParams.get("code");

   if (
    !state ||
    !savedState ||
    state !== savedState ||
    !code
   ) {
    return new Response(
     "OAuth state check failed. Return to the homepage and try again.",
     {status:400}
    );
   }

   await env.TOKENS.delete("oauth_state");

   const tokenResponse = await fetch(
    "https://oauth2.googleapis.com/token",
    {
     method:"POST",
     headers:{
      "content-type":"application/x-www-form-urlencoded"
     },
     body:new URLSearchParams({
      code,
      client_id:env.GOOGLE_CLIENT_ID,
      client_secret:env.GOOGLE_CLIENT_SECRET,
      redirect_uri:url.origin + "/oauth/callback",
      grant_type:"authorization_code"
     })
    }
   );

   const tokenData = await tokenResponse.json();

   if (!tokenResponse.ok) {
    return new Response(
     "Google authorization failed: " +
     (tokenData.error_description || tokenData.error || "Unknown error"),
     {status:400}
    );
   }

   if (!tokenData.refresh_token) {
    return new Response(
     "Google did not provide a new refresh token. Your existing connection was preserved. Try reconnecting with Google consent again.",
     {status:400}
    );
   }

   // Save the new refresh token with both requested permissions.
   await env.TOKENS.put(
    "youtube_refresh_token",
    tokenData.refresh_token
   );

   return Response.redirect(
    url.origin + "/?connected=1",
    302
   );
  }

  // Connection status
  if (path === "/api/status") {
   const refreshToken = await env.TOKENS.get(
    "youtube_refresh_token"
   );

   return json({
    connected:!!refreshToken
   });
  }

  // Protected GitHub Actions configuration endpoint
  if (path === "/internal/config") {
   if (
    request.headers.get("authorization") !==
    "Bearer " + env.SCHEDULER_SECRET
   ) {
    return json({error:"unauthorized"},401);
   }

   const refreshToken = await env.TOKENS.get(
    "youtube_refresh_token"
   );

   if (!refreshToken) {
    return json({error:"Google account is not connected"},404);
   }

   return json({
    youtube_refresh_token:refreshToken,
    youtube_client_id:env.GOOGLE_CLIENT_ID,
    youtube_client_secret:env.GOOGLE_CLIENT_SECRET
   });
  }

  return new Response("Not found",{status:404});
 }
};
