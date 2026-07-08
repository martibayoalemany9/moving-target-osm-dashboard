import { auth, isAuthRequired, isGoogleAuthConfigured, signIn, signOut } from "../auth";
import DashboardClient from "./ui/DashboardClient";

export default async function Page() {
  const authRequired = isAuthRequired();
  const googleAuthConfigured = isGoogleAuthConfigured();
  const session = await auth();
  if (authRequired && !googleAuthConfigured) {
    return (
      <main className="login">
        <section>
          <h1>Moving Target OSM Dashboard</h1>
          <p>Google auth is enabled, but real OAuth credentials are not configured.</p>
          <p>Set `AUTH_GOOGLE_ID`, `AUTH_GOOGLE_SECRET`, `AUTH_SECRET`, and use the auth port callback URL.</p>
        </section>
      </main>
    );
  }

  if (authRequired && !session?.user) {
    return (
      <main className="login">
        <section>
          <h1>Moving Target OSM Dashboard</h1>
          <p>Sign in with Google to access the dashboard.</p>
          <form action={async () => {
            "use server";
            await signIn("google");
          }}>
            <button type="submit">Sign in with Google</button>
          </form>
        </section>
      </main>
    );
  }

  return (
    <>
      <header className="app-header">
        <div>
          <h1>Moving Target OSM Dashboard</h1>
          <span>{session?.user?.email || "public local mode"}</span>
        </div>
        {authRequired ? (
          <form action={async () => {
            "use server";
            await signOut();
          }}>
            <button type="submit">Sign out</button>
          </form>
        ) : null}
      </header>
      <DashboardClient />
    </>
  );
}
