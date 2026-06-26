import { displayNameOf } from "../displayName";

describe("displayNameOf", () => {
  test("returns empty string when user is null/undefined", () => {
    expect(displayNameOf(null)).toBe("");
    expect(displayNameOf(undefined)).toBe("");
  });

  test("returns user.name when set", () => {
    expect(displayNameOf({ name: "Ada Lovelace", email: "ada@example.com" })).toBe("Ada Lovelace");
  });

  test("trims whitespace from name", () => {
    expect(displayNameOf({ name: "  Ada  ", email: "x@y.com" })).toBe("Ada");
  });

  test("returns 'Apple user' for private-relay emails when no name", () => {
    expect(displayNameOf({ email: "vr26bc7jntw@privaterelay.appleid.com" })).toBe("Apple user");
    expect(displayNameOf({ email: "ABC@PRIVATERELAY.APPLEID.COM" })).toBe("Apple user");
  });

  test("returns email for regular emails when no name", () => {
    expect(displayNameOf({ email: "ada@example.com" })).toBe("ada@example.com");
  });

  test("prefers name over relay-email handling", () => {
    expect(displayNameOf({ name: "Ada", email: "x@privaterelay.appleid.com" })).toBe("Ada");
  });

  test("falls back to empty string when no name and no email", () => {
    expect(displayNameOf({})).toBe("");
  });
});
