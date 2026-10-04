// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { I18nProvider, LOCALE_META, useI18n } from "@/i18n";
import type { StatusResponse } from "@/lib/api";
import { SidebarFooter } from "./SidebarFooter";

let container: HTMLDivElement;
let root: Root;

function ProductLabels() {
  const { t } = useI18n();
  return (
    <div data-brand={t.app.brand}>
      <span data-label="update">{t.status.updateHermes}</span>
      <span data-label="plugins">{t.pluginsPage.headline}</span>
      <span data-label="achievements">{t.achievements.hero.title}</span>
      <span data-label="config">{t.config.configPath}</span>
    </div>
  );
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  localStorage.clear();
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  localStorage.clear();
});

describe("dashboard product identity", () => {
  it.each(Object.keys(LOCALE_META))("keeps ownership and product labels consistent in %s", async (locale) => {
    localStorage.setItem("hermes-locale", locale);
    const status = { version: "2.4.6-test" } as StatusResponse;
    await act(async () => root.render(
      <I18nProvider>
        <SidebarFooter status={status} />
        <ProductLabels />
      </I18nProvider>,
    ));

    const owner = container.querySelector("a")!;
    expect(owner.textContent).toBe("AetherMesh");
    expect(owner.href).toBe("https://github.com/AetherMesh-AI/Eidolon");
    expect(owner.rel).toContain("noopener");
    expect(container.textContent).toContain(`v${status.version}`);
    expect(document.documentElement.lang).toBe(locale);

    const brand = container.querySelector("[data-brand]")!.getAttribute("data-brand")!;
    for (const label of ["update", "plugins", "achievements"]) {
      expect(container.querySelector(`[data-label="${label}"]`)!.textContent).toContain(brand);
    }
    expect(container.querySelector('[data-label="plugins"]')!.textContent).toContain("eidolon plugins");
    expect(container.querySelector('[data-label="config"]')!.textContent).toBe("~/.hermes/config.yaml");
    expect(localStorage.getItem("hermes-locale")).toBe(locale);
  });

  it("keeps ownership visible when version information is unavailable", async () => {
    await act(async () => root.render(
      <I18nProvider><SidebarFooter status={null} /></I18nProvider>,
    ));
    expect(container.querySelector("a")!.textContent).toBe("AetherMesh");
    expect(container.textContent).toContain("—");
  });
});
