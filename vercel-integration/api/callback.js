/**
 * Vercel Integration callback handler.
 *
 * When a user installs the MAXIA integration from the Vercel Marketplace,
 * this endpoint is called to complete the OAuth flow and provision a
 * MAXIA API key for the user's project.
 */

export default async function handler(req, res) {
  const { code, configurationId, teamId } = req.query;

  if (!code) {
    return res.status(400).json({ error: "Missing authorization code" });
  }

  // Exchange code for access token with Vercel
  const tokenRes = await fetch("https://api.vercel.com/v2/oauth/access_token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      client_id: process.env.VERCEL_CLIENT_ID,
      client_secret: process.env.VERCEL_CLIENT_SECRET,
      code,
      redirect_uri: `${process.env.HOST}/api/callback`,
    }),
  });

  if (!tokenRes.ok) {
    return res.status(500).json({ error: "Failed to exchange token" });
  }

  const { access_token, team_id } = await tokenRes.json();

  // Register a MAXIA API key for this integration
  const maxiaRes = await fetch("https://maxiaworld.app/api/public/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: `Vercel Integration ${configurationId}`,
      wallet: "",
      description: `Auto-provisioned via Vercel Marketplace (team: ${teamId || team_id || "personal"})`,
      capabilities: ["discover", "execute", "swap", "prices"],
    }),
  });

  const maxiaData = await maxiaRes.json();
  const maxiaApiKey = maxiaData.api_key || "";

  // Set MAXIA_API_KEY as environment variable in the user's Vercel project
  if (maxiaApiKey && access_token) {
    await fetch(`https://api.vercel.com/v10/projects/${configurationId}/env`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${access_token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        key: "MAXIA_API_KEY",
        value: maxiaApiKey,
        type: "encrypted",
        target: ["production", "preview", "development"],
      }),
    });
  }

  // Redirect to MAXIA dashboard
  res.redirect(302, "https://maxiaworld.app?source=vercel&configured=true");
}
