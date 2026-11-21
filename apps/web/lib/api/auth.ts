import type { ApiClient } from "./client.ts";
import type { Locale, RegisterInput, User } from "./types.ts";

// Sign-in, refresh and sign-out do not live here: they go through the web's own session routes
// (lib/session/browser.ts), which keep the refresh token in an HttpOnly cookie (plan 4.1).

export async function register(api: ApiClient, input: RegisterInput): Promise<User> {
  return (await api.request<User>("POST", "/auth/register", { json: input, auth: false })).data;
}

/** Always answers the same, registered email or not (the server does not reveal accounts). */
export async function forgotPassword(api: ApiClient, email: string): Promise<void> {
  await api.request<void>("POST", "/auth/password/forgot", { json: { email }, auth: false });
}

export async function resetPassword(api: ApiClient, token: string, newPassword: string): Promise<void> {
  await api.request<void>("POST", "/auth/password/reset", { json: { token, new_password: newPassword }, auth: false });
}

export async function me(api: ApiClient): Promise<User> {
  return (await api.request<User>("GET", "/me")).data;
}

export async function setLocale(api: ApiClient, locale: Locale): Promise<User> {
  return (await api.request<User>("PATCH", "/me", { json: { locale } })).data;
}
