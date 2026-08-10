import { defineConfig } from "vitest/config"
import path from "node:path"

// Unit tests for the pure logic in lib/.
//
// Deliberately narrow: this is not a component-testing setup and does not try to
// render React. The things worth testing here are the functions that decide what
// a reader sees and what happens to their data — a mute list that fails open, or
// a "clear" that clears the wrong key, are silent failures no browser check I
// can run will reliably catch.
//
// happy-dom rather than jsdom for localStorage, window events and Blob/URL, at
// roughly a fifth of the install size.
export default defineConfig({
  test: {
    environment: "happy-dom",
    include: ["lib/**/*.test.ts"],
    // Each file gets a clean localStorage; these modules are all about
    // persistence, so leakage between tests would make them lie.
    restoreMocks: true,
  },
  resolve: {
    alias: { "@": path.resolve(__dirname, "./") },
  },
})
