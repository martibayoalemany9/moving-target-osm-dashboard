import { NextResponse } from "next/server";
import { auth, isGoogleAuthConfigured } from "./auth";

export const middleware = isGoogleAuthConfigured()
  ? auth
  : function localDevelopmentMiddleware() {
      return NextResponse.next();
    };

export const config = {
  matcher: ["/((?!api/auth|_next/static|_next/image|favicon.ico).*)"]
};
