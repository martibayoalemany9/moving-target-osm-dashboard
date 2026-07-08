import NextAuth from "next-auth";
import Google from "next-auth/providers/google";

const GOOGLE_ID_PLACEHOLDER = "google-oauth-client-id.apps.googleusercontent.com";
const GOOGLE_SECRET_PLACEHOLDER = "google-oauth-client-secret";

export function isGoogleAuthConfigured() {
  const id = process.env.AUTH_GOOGLE_ID || "";
  const secret = process.env.AUTH_GOOGLE_SECRET || "";
  return Boolean(id && secret && id !== GOOGLE_ID_PLACEHOLDER && secret !== GOOGLE_SECRET_PLACEHOLDER);
}

export function isAuthRequired() {
  return process.env.AUTH_REQUIRED === "true";
}

export const { handlers, auth, signIn, signOut } = NextAuth({
  providers: isGoogleAuthConfigured() ? [Google] : [],
  callbacks: {
    authorized({ auth: session }) {
      if (!isAuthRequired()) return true;
      if (!isGoogleAuthConfigured()) return true;
      return Boolean(session?.user);
    },
    signIn({ profile }) {
      const allowedDomain = process.env.NEXT_PUBLIC_ALLOWED_DOMAIN;
      const email = profile?.email || "";
      if (!allowedDomain) return true;
      return email.endsWith(`@${allowedDomain}`);
    }
  }
});
