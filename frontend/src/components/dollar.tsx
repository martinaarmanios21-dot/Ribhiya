import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { useEffect, useState } from "react";
import { Card, Loading, Pill, Spinner, useErrorToast } from "@/components/bits";
import { useIT } from "@/components/charts";
import { useApp } from "@/lib/app-context";
import { papi2, xapi } from "@/lib/tapi";
import { cn } from "@/lib/utils";

/* eslint-disable @typescript-eslint/no-explicit-any */
const pct = (x: number | null | undefined) => (x == null ? "—" : `${(x * 100).toFixed(0)}%`);
const TONE: Record<string, "danger" | "warn" | "ok"> = { below_cost: "danger", squeezed: "warn", ok: "ok" };

/** Dollar protection: margin alert, the price that keeps each product's margin, and the restock warning. */
export function Dollar({ id }: { id: string }) {
  const it = useIT();
  const { lang, money } = useApp();
  const qc = useQueryClient();
  const onErr = useErrorToast();
  const q = useQuery({ queryKey: ["fx", id, lang], queryFn: () => xapi.fx(id, lang) });
  const [f, setF] = useState<any>({ base_rate: "", current_rate: "", all: "100", per: {}, target: "" });
  const [busy, setBusy] = useState(false);
  const [sim, setSim] = useState<{ dollar?: any; prices?: any } | null>(null);
  useEffect(() => {  // show what the owner saved last time
    const s = q.data?.settings;
    if (!s) return;
    const { all = 0, ...per } = s.linked ?? {};
    setF({ base_rate: String(s.base_rate), current_rate: String(s.current_rate), all: String(all),
      per: Object.fromEntries(Object.entries(per).map(([k, v]) => [k, String(v)])), target: s.target_margin_pct == null ? "" : String(s.target_margin_pct) });
  }, [q.data?.settings]);
  if (!q.data) return <Loading />;
  const d = q.data, cur = d.currency;
  if (!d.available) return <Card title={it("fx_title")}><p className="text-sm" dir="auto">{d.reason}</p></Card>;

  const save = async () => {
    setBusy(true); setSim(null);
    const linked: Record<string, number> = { all: Number(f.all || 0) };
    for (const [k, v] of Object.entries(f.per)) if (v !== "" && v != null) linked[k] = Number(v);
    try {
      await xapi.setFx(id, { base_rate: f.base_rate, current_rate: f.current_rate, linked, target_margin_pct: f.target === "" ? null : f.target });
      await qc.invalidateQueries({ queryKey: ["fx", id] });
    } catch (e) { onErr(e); } finally { setBusy(false); }
  };
  const trySim = async () => {
    const s = d.settings;
    const dollar = { base: String(s.base_rate), new: String(s.current_rate), linked: s.linked };
    setBusy(true);
    try {
      const a = await papi2.simulate(id, { dollar_rate: dollar }, d.period, lang);
      const b = Object.keys(d.price_scenario ?? {}).length
        ? await papi2.simulate(id, { dollar_rate: dollar, price_change_pct: d.price_scenario }, d.period, lang) : null;
      setSim({ dollar: a, prices: b });
    } catch (e) { onErr(e); } finally { setBusy(false); }
  };
  const input = "w-28 rounded-lg border bg-card px-2 py-1.5 text-sm";
  const below = (d.products ?? []).filter((p: any) => p.status === "below_cost");

  return (
    <div className="grid gap-4">
      <Card title={it("fx_title")}>
        <p className="-mt-2 mb-3 text-sm text-muted-foreground">{it("fx_intro")}</p>
        <div className="grid gap-2 md:grid-cols-2">
          <label className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-muted/50 px-3 py-2 text-sm">{it("fx_base")}
            <input inputMode="decimal" dir="ltr" className={input} value={f.base_rate} placeholder="48.50" onChange={(e) => setF({ ...f, base_rate: e.target.value })} /></label>
          <label className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-muted/50 px-3 py-2 text-sm">
            <span>{it("fx_now")}<span className="block text-xs text-muted-foreground">{it("fx_now_hint")}</span></span>
            <input inputMode="decimal" dir="ltr" className={input} value={f.current_rate} placeholder="52.00" onChange={(e) => setF({ ...f, current_rate: e.target.value })} /></label>
          <label className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-muted/50 px-3 py-2 text-sm">
            <span>{it("fx_linked_all")}<span className="block text-xs text-muted-foreground">{it("fx_linked_hint")}</span></span>
            <input inputMode="decimal" dir="ltr" className={input} value={f.all} onChange={(e) => setF({ ...f, all: e.target.value })} /></label>
          <label className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-muted/50 px-3 py-2 text-sm">
            <span>{it("fx_target")}<span className="block text-xs text-muted-foreground">{it("fx_target_hint")}</span></span>
            <input inputMode="decimal" dir="ltr" className={input} value={f.target} onChange={(e) => setF({ ...f, target: e.target.value })} /></label>
        </div>
        <details className="mt-2 text-sm"><summary className="cursor-pointer font-medium">{it("fx_per_product")}</summary>
          <div className="mt-2 grid gap-1 md:grid-cols-2">{(d.cost_products ?? []).map((p: string) => (
            <label key={p} className="flex items-center justify-between gap-2 rounded bg-muted/30 px-2 py-1"><span dir="auto">{p}</span>
              <span className="flex items-center gap-1"><input inputMode="decimal" dir="ltr" className="w-16 rounded border bg-card px-2 py-1 text-sm" placeholder={f.all}
                value={f.per[p] ?? ""} onChange={(e) => setF({ ...f, per: { ...f.per, [p]: e.target.value } })} />%</span></label>))}
          </div>
        </details>
        <button className="btn btn-primary mt-3 !py-2" disabled={busy || !f.base_rate || !f.current_rate} onClick={save}>{it("fx_save")}</button>
        <Spinner show={busy} />
      </Card>

      {d.summary && (
        <Card>
          <p className={cn("rounded-lg p-3 text-sm font-medium", below.length ? "bg-danger/10" : d.monthly_impact < 0 ? "bg-warning/10" : "bg-primary-soft")} dir="auto">{d.summary}</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Pill tone={d.monthly_impact < 0 ? "danger" : "ok"}>{it("fx_monthly")}: <b dir="ltr">{money(Math.abs(d.monthly_impact), cur)}</b></Pill>
            {!!d.counts.below_cost && <Pill tone="danger">{d.counts.below_cost} {it("fx_below_n")}</Pill>}
            {!!d.counts.squeezed && <Pill tone="warn">{d.counts.squeezed} {it("fx_squeezed_n")}</Pill>}
          </div>
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr className="text-xs text-muted-foreground">
                {["fx_product", "fx_linked", "fx_price", "fx_cost", "fx_margin", "fx_suggested", "fx_breakeven", ""].map((k) =>
                  <th key={k} className="border-b p-2 text-start">{k && it(k)}</th>)}
              </tr></thead>
              <tbody>{d.products.map((p: any) => (
                <tr key={p.product} className="border-b">
                  <td className="p-2" dir="auto">{p.product}</td>
                  <td className="p-2 tabular-nums" dir="ltr">{p.linked_pct}%</td>
                  <td className="p-2 tabular-nums" dir="ltr">{money(p.price, cur)}</td>
                  <td className="p-2 tabular-nums" dir="ltr">{money(p.unit_cost, cur)} → <b>{money(p.new_unit_cost, cur)}</b></td>
                  <td className="p-2 tabular-nums" dir="ltr">{pct(p.margin)} → <b className={p.new_margin < 0 ? "text-danger" : ""}>{pct(p.new_margin)}</b></td>
                  <td className="p-2 tabular-nums" dir="ltr">{p.suggested_price && p.raise_pct ? <><b>{money(p.suggested_price, cur)}</b> <span className="text-xs text-muted-foreground">({p.raise_pct > 0 ? "+" : ""}{p.raise_pct}%)</span>
                    {p.keep_profit_price && p.keep_profit_price < p.suggested_price && <span className="block text-xs text-muted-foreground">{it("fx_keep_profit")}: {money(p.keep_profit_price, cur)}</span>}</> : "—"}</td>
                  <td className="p-2 tabular-nums" dir="ltr">{p.break_even_rate ?? "—"}</td>
                  <td className="p-2"><Pill tone={TONE[p.status] ?? "muted"}>{it(`fx_s_${p.status}`)}</Pill></td>
                </tr>))}</tbody>
            </table>
          </div>
          {!!d.skipped_no_cost?.length && <p className="mt-2 text-xs text-warning" dir="auto">{it("fx_skipped", { list: d.skipped_no_cost.join("، ") })}</p>}
          <p className="mt-2 text-xs text-muted-foreground"><b>{it("fx_assumptions")}:</b> {d.assumptions.join(" · ")}</p>
        </Card>
      )}

      {!!below.length && (
        <Card title={<span className="flex items-center gap-2 text-danger"><AlertTriangle className="size-5" />{it("fx_restock_title")}</span>}>
          <ul className="grid gap-2 text-sm">{below.map((p: any) => (
            <li key={p.product} className="rounded-lg bg-danger/5 p-2" dir="auto">{it("fx_restock_line", { p: p.product, price: money(p.price, cur), cost: money(p.new_unit_cost, cur), loss: money(p.loss_per_unit, cur) })}</li>))}</ul>
          <p className="mt-2 text-sm text-muted-foreground">{it("fx_restock_tip")}</p>
        </Card>
      )}

      {!!d.stress?.length && (
        <Card title={it("fx_stress_title")}>
          <ul className="grid gap-1 text-sm">{d.stress.map((s: any) => (
            <li key={s.step_pct} className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-muted/50 px-3 py-2">
              <span>{it("fx_stress_row", { rate: s.rate.toFixed(2), pct: s.step_pct })}</span>
              <span className="flex items-center gap-2"><b className="text-danger tabular-nums" dir="ltr">−{money(Math.abs(s.monthly_impact), cur)}</b>
                {!!s.below_cost && <Pill tone="danger">{it("fx_stress_below", { n: s.below_cost })}</Pill>}</span>
            </li>))}</ul>
        </Card>
      )}

      {d.settings && !!d.products?.length && (
        <Card title={it("fx_sim_title")}>
          <p className="-mt-2 mb-3 text-sm text-muted-foreground">{it("fx_sim_sub")}</p>
          <button className="btn btn-outline !py-2" disabled={busy} onClick={trySim}>{it("sim_run")}</button>
          {sim && (
            <div className="mt-3 grid gap-3 md:grid-cols-2">
              {[["fx_sim_dollar", sim.dollar], ["fx_sim_prices", sim.prices]].filter(([, r]) => r).map(([k, r]: any) => (
                <div key={k} className="rounded-lg border p-3 text-sm">
                  <p className="font-semibold">{it(k)}</p>
                  <p className="mt-1" dir="auto">{r.summary}</p>
                  <p className="mt-2">{it("fx_sim_after_fixed")}: <b dir="ltr" className={(r.projected.after_fixed ?? 0) < 0 ? "text-danger" : "text-success"}>
                    {r.projected.after_fixed == null ? "—" : money(r.projected.after_fixed, cur)}</b>
                    {r.baseline.after_fixed != null && <span className="text-xs text-muted-foreground" dir="ltr"> ({money(r.baseline.after_fixed, cur)})</span>}</p>
                  <ul className="mt-2 list-disc ps-4 text-xs text-muted-foreground">{r.assumptions.map((a: string) => <li key={a}>{a}</li>)}</ul>
                </div>))}
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
