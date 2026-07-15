import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Positions } from "../Positions";
import type { Position } from "../../api";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return {
    ...actual,
    closePosition: vi.fn(),
  };
});

import { closePosition } from "../../api";

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
  funding_paid: 0.5,
  entry_time: "2026-07-15T10:00:00Z",
  ai_confidence: 0.75,
  ai_reason: "uptrend",
  mode: "ai",
  liq_price: 40201,
  ...over,
});

describe("Positions", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows empty state when no positions", () => {
    render(<Positions positions={[]} />);
    expect(screen.getByText("当前无持仓")).toBeInTheDocument();
  });

  it("renders liquidation price column", () => {
    render(<Positions positions={[makePos()]} />);
    expect(screen.getByText("$40,201")).toBeInTheDocument();
  });

  it("shows mode label (AI / 趋势 / 手动)", () => {
    render(<Positions positions={[makePos({ mode: "manual" })]} />);
    expect(screen.getByText("手动")).toBeInTheDocument();
  });

  it("calls onRowClick when a row is clicked", async () => {
    const onRowClick = vi.fn();
    render(<Positions positions={[makePos()]} onRowClick={onRowClick} />);
    // Click on the pair cell (not the close button)
    await userEvent.click(screen.getByText("BTC/USDT"));
    expect(onRowClick).toHaveBeenCalledWith(expect.objectContaining({ id: 1 }));
  });

  it("opens confirm dialog on close button click", async () => {
    render(<Positions positions={[makePos()]} />);
    await userEvent.click(screen.getByRole("button", { name: "平仓" }));
    expect(screen.getByRole("button", { name: "确认平仓" })).toBeInTheDocument();
    expect(screen.getByText(/当前浮盈/)).toBeInTheDocument();
  });

  it("cancel confirm does not call closePosition", async () => {
    render(<Positions positions={[makePos()]} />);
    await userEvent.click(screen.getByRole("button", { name: "平仓" }));
    await userEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(closePosition).not.toHaveBeenCalled();
  });

  it("confirm calls closePosition and onClosed", async () => {
    vi.mocked(closePosition).mockResolvedValue({ success: true });
    const onClosed = vi.fn();
    render(<Positions positions={[makePos()]} onClosed={onClosed} />);
    await userEvent.click(screen.getByRole("button", { name: "平仓" }));
    await userEvent.click(screen.getByRole("button", { name: "确认平仓" }));
    await waitFor(() => {
      expect(closePosition).toHaveBeenCalledWith(1);
      expect(onClosed).toHaveBeenCalled();
    });
  });

  it("shows error toast on close failure", async () => {
    vi.mocked(closePosition).mockRejectedValue(new Error("broker rejected"));
    render(<Positions positions={[makePos()]} />);
    await userEvent.click(screen.getByRole("button", { name: "平仓" }));
    await userEvent.click(screen.getByRole("button", { name: "确认平仓" }));
    await waitFor(() => {
      expect(screen.getByText(/平仓失败/)).toBeInTheDocument();
    });
  });
});
