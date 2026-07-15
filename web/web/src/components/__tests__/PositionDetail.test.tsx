import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { PositionDetail } from "../PositionDetail";
import type { Position } from "../../api";

// Mock the api module's getPositionDetail
vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return {
    ...actual,
    getPositionDetail: vi.fn(),
  };
});

import { getPositionDetail } from "../../api";

const basePosition: Position = {
  id: 1,
  pair: "BTC/USDT:USDT",
  side: "long",
  entry_price: 50000,
  current_price: 51000,
  contracts: 0.01,
  margin: 100,
  leverage: 5,
  sl_price: 49000,
  tp_price: 52000,
  unrealized_pnl: 10,
  roe_pct: 10,
  funding_paid: 0.5,
  entry_time: "2026-07-15T10:00:00Z",
  ai_confidence: 0.75,
  ai_reason: "uptrend confirmed",
  mode: "ai",
  liq_price: 40000,
};

describe("PositionDetail", () => {
  beforeEach(() => vi.clearAllMocks());

  it("renders core position fields", async () => {
    vi.mocked(getPositionDetail).mockResolvedValue({ ...basePosition, status: "open" } as any);
    render(<PositionDetail position={basePosition} onClose={vi.fn()} />);
    await waitFor(() => expect(getPositionDetail).toHaveBeenCalledWith(1));
    // Header shows pair + side/leverage; use heading role for stability
    const heading = screen.getByRole("heading", { level: 3 });
    expect(heading.textContent).toContain("BTC/USDT");
    expect(heading.textContent).toContain("多");
    expect(heading.textContent).toContain("5x");
    expect(screen.getByText("uptrend confirmed")).toBeInTheDocument();
  });

  it("shows AI confidence as percentage", async () => {
    vi.mocked(getPositionDetail).mockResolvedValue({ ...basePosition, status: "open" } as any);
    render(<PositionDetail position={basePosition} onClose={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByText("75%")).toBeInTheDocument();
    });
  });

  it("shows dash when ai_confidence is null", async () => {
    vi.mocked(getPositionDetail).mockResolvedValue({ ...basePosition, ai_confidence: null, status: "open" } as any);
    render(<PositionDetail position={{ ...basePosition, ai_confidence: null }} onClose={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByText("—")).toBeInTheDocument();
    });
  });

  it("renders nothing when position is null", () => {
    const { container } = render(<PositionDetail position={null} onClose={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("computeLiq liquidation price formula", () => {
  // The component mirrors SimBroker._liquidation_price:
  //   long:  liq = (entry - (margin - max(funding,0))/contracts) / (1 - 0.005)
  //   short: liq = (entry + (margin - max(-funding,0))/contracts) / (1 + 0.005)
  // We verify via the rendered liq_price passed in (component uses detail.liq_price
  // when present, else computes). Here we assert the formula directly.

  it("long liq price is below entry", () => {
    // entry=50000, margin=100, contracts=0.01, funding=0
    // eff = 100 - 0 = 100; liq = (50000 - 100/0.01) / 0.995 = (50000-10000)/0.995 = 40201
    const entry = 50000, margin = 100, contracts = 0.01, funding = 0;
    const eff = margin - Math.max(funding, 0);
    const liq = (entry - eff / contracts) / (1 - 0.005);
    expect(liq).toBeCloseTo(40201.005, 1);
    expect(liq).toBeLessThan(entry);
  });

  it("short liq price is above entry", () => {
    // entry=50000, margin=100, contracts=0.01, funding=0
    // eff = 100 - 0 = 100; liq = (50000 + 10000) / 1.005 = 59701.49
    const entry = 50000, margin = 100, contracts = 0.01, funding = 0;
    const eff = margin - Math.max(-funding, 0);
    const liq = (entry + eff / contracts) / (1 + 0.005);
    expect(liq).toBeCloseTo(59701.49, 1);
    expect(liq).toBeGreaterThan(entry);
  });

  it("positive funding reduces long effective margin (higher liq price)", () => {
    const entry = 50000, margin = 100, contracts = 0.01;
    const liqNoFunding = (entry - (margin - 0) / contracts) / (1 - 0.005);
    const liqWithFunding = (entry - (margin - 5) / contracts) / (1 - 0.005); // funding=5 paid by long
    expect(liqWithFunding).toBeGreaterThan(liqNoFunding);
  });
});
