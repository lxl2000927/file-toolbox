// @vitest-environment happy-dom
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import SideNav from "./SideNav.vue";

describe("SideNav", () => {
  it("disables and does not emit unavailable navigation", async () => {
    const wrapper = mount(SideNav, { props: { active: "rename", disabled: ["pdf_split", "scan_split"] } });
    const pdf = wrapper.get('[data-nav-key="pdf_split"]');
    expect(pdf.attributes("disabled")).toBeDefined();
    await pdf.trigger("click");
    expect(wrapper.emitted("navigate")).toBeUndefined();
  });
});
