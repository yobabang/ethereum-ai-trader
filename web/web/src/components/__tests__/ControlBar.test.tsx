import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ControlBar } from "../ControlBar";
import type { Position } from "../../api";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return {
    ...actual,
    closeAllPositions: vi.fn(),
    placeManualOrder: vi.fn(),
  };
});

import { closeAllPositions, placeManualOrder } from "../../api";

const makePos = (over: Partial<Position> = {}): Position => ({
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
  unrealized_pnl: 25,
  roe_pct: 25,
  funding_paid: 0,
  entry_time: "2026-07-15T10:00:00Z",
  ai_confidence: null,
  ai_reason: null,
  mode: "ai",
  liq_price: 40000,
  ...over,
});

describe("ControlBar", () => {
  beforeEach(() => vi.clearAllMocks());

  it("disables close-all button when no positions", () => {
    render(<ControlBar positions={[]} />);
    const btn = screen.getByText(/一键平仓所有/);
    expect(btn).toBeDisabled();
  });

  it("enables close-all and shows count when positions exist", () => {
    render(<ControlBar positions={[makePos()]} />);
    const btn = screen.getByText(/一键平仓所有 \(1\)/);
    expect(btn).not.toBeDisabled();
  });

  it("close-all requires confirmation then calls closeAllPositions + onAction", async () => {
    vi.mocked(closeAllPositions).mockResolvedValue({ closed: 1, failed: 0 });
    const onAction = vi.fn();
    render(<ControlBar positions={[makePos()]} onAction={onAction} />);
    await userEvent.click(screen.getByText(/一键平仓所有/));
    expect(screen.getByText("确认一键平仓")).toBeInTheDocument();
    await userEvent.click(screen.getByText("确认全平"));
    await waitFor(() => {
      expect(closeAllPositions).toHaveBeenCalledWith([expect.objectContaining({ id: 1 })]);
      expect(onAction).toHaveBeenCalled();
    });
  });

  it("cancel close-all does not call closeAllPositions", async () => {
    render(<ControlBar positions={[makePos()]} />);
    await userEvent.click(screen.getByText(/一键平仓所有/));
    await userEvent.click(screen.getByText("取消"));
    expect(closeAllPositions).not.toHaveBeenCalled();
  });

  it("manual open calls placeManualOrder with form defaults (10x / 10% / 0.8% / 1.5%)", async () => {
    vi.mocked(placeManualOrder).mockResolvedValue({ success: true, position_id: 5 });
    const onAction = vi.fn();
    render(<ControlBar positions={[]} onAction={onAction} />);
    await userEvent.click(screen.getByText("开仓（覆盖）"));
    await waitFor(() => {
      expect(placeManualOrder).toHaveBeenCalledWith(
        expect.objectContaining({
          pair: "BTC/USDT:USDT",
          side: "long",
          position_size_pct: 0.10,
          leverage: 10,
          stop_loss_pct: 0.008,
          take_profit_pct: 0.015,
        })
      );
      expect(onAction).toHaveBeenCalled();
    });
  });

  it("shows rejection reason when broker refuses the order", async () => {
    vi.mocked(placeManualOrder).mockResolvedValue({
      success: false,
      reason: "confidence below threshold",
    });
    render(<ControlBar positions={[]} />);
    await userEvent.click(screen.getByText("开仓（覆盖）"));
    await waitFor(() => {
      expect(screen.getByText(/confidence below threshold/)).toBeInTheDocument();
    });
  });

  it("switching to short passes side=short", async () => {
    vi.mocked(placeManualOrder).mockResolvedValue({ success: true, position_id: 6 });
    render(<ControlBar positions={[]} />);
    await userEvent.click(screen.getByText("做空"));
    await userEvent.click(screen.getByText("开仓（覆盖）"));
    await waitFor(() => {
      expect(placeManualOrder).toHaveBeenCalledWith(
        expect.objectContaining({ side: "short" })
      );
    });
  });
});
