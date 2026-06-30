import NextAuth from "next-auth";
import Google from "next-auth/providers/google";

export const { handlers, auth, signIn, signOut } = NextAuth({
  providers: [Google],
  callbacks: {
    authorized({ auth: session }) {
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
