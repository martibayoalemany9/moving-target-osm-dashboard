import { auth, signIn, signOut } from "../auth";
import DashboardClient from "./ui/DashboardClient";

export default async function Page() {
  const session = await auth();
  if (!session?.user) {
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
          <span>{session.user.email}</span>
        </div>
        <form action={async () => {
          "use server";
          await signOut();
        }}>
          <button type="submit">Sign out</button>
        </form>
      </header>
      <DashboardClient />
    </>
  );
}
