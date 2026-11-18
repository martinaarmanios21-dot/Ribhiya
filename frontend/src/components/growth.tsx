import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { Copy } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Card, Confirm, Loading, Pill, useErrorToast } from "@/components/bits";
import { useIT } from "@/components/charts";
import { useApp } from "@/lib/app-context";
import { xapi } from "@/lib/tapi";

const pct = (x: number | null | undefined) => (x == null ? "—" : `${Math.round(x * 100)}%`);

/** Revenue opportunities: win back lapsed customers (with a comparison group to measure the real effect), bundles. */
export function Growth({ id }: { id: string }) {
  const it = useIT();
  const { lang, money } = useApp();
  const q = useQuery({ queryKey: ["opps", id, lang], queryFn: () => xapi.opportunities(id, lang) });
  if (!q.data) return <Loading />;
  const d = q.data, c = d.customers, cur = d.currency;
  const fill = (row: any) => c.message_template.replace("{name}", row.customer).replace("{favourite}", row.favourite ?? "");
  const copy = (row: any) => navigator.clipboard?.writeText(fill(row)).then(() => toast.success(it("gr_copied")));
  return (
    <div className="grid gap-4">
      {!c.available ? (
        <Card title={it("gr_title")}><p className="text-sm" dir="auto">{c.reason}</p><p className="mt-1 text-xs text-muted-foreground" dir="auto">{c.how_to}</p></Card>
      ) : (
        <Card title={it("gr_title")}>
          <p className="text-sm text-muted-foreground">{it("gr_intro")}</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Pill tone="primary">{it("gr_repeat")}: {pct(c.repeat_rate)}</Pill>
            <Pill tone="primary">{it("gr_top20")}: {pct(c.top20_share)}</Pill>
            {c.typical_gap_days != null && <Pill>{it("gr_gap")} {Math.round(c.typical_gap_days)} {it("gr_days")}</Pill>}
          </div>
          {c.preliminary && <p className="mt-2 text-xs text-warning" dir="auto">{c.preliminary_reason}</p>}
          {c.winback && <p className="mt-3 rounded-lg bg-primary-soft p-3 text-sm" dir="auto">{c.winback.text}</p>}
          {c.result && (
            <div className="mt-3 rounded-lg border p-3 text-sm">
              <p className="font-semibold">{it("gr_result")}</p>
              <p dir="auto">{c.result.text}</p>
            </div>
          )}
          {!c.lapsed.length ? <p className="mt-3 text-sm">{it("gr_none")}</p> : (
            <div className="mt-3 overflow-x-auto">
              <table className="w-full text-sm">
                <thead><tr className="text-start text-xs text-muted-foreground">
                  <th className="p-2 text-start">{it("gr_customer")}</th><th className="p-2 text-start">{it("gr_last")}</th>
                  <th className="p-2 text-start">{it("gr_spent")}</th><th className="p-2 text-start">{it("gr_fav")}</th>
                  <th className="p-2 text-start">{it("gr_group")}</th><th className="p-2" />
                </tr></thead>
                <tbody>
                  {c.lapsed.map((r: any) => (
                    <tr key={r.customer} className="border-t">
                      <td className="p-2" dir="auto">{r.customer}</td><td className="p-2">{r.last_order}</td>
                      <td className="p-2">{money(r.spent, cur)}</td><td className="p-2" dir="auto">{r.favourite}</td>
                      <td className="p-2"><Pill tone={r.group === "message" ? "ok" : "muted"}>{it(`gr_g_${r.group}`)}</Pill></td>
                      <td className="p-2">{r.group === "message" && (
                        <button className="btn btn-outline !py-1 text-xs" onClick={() => copy(r)}><Copy className="size-3" /> {it("gr_copy")}</button>)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <details className="mt-3 text-sm"><summary className="cursor-pointer font-medium">{it("gr_measure")}</summary>
            <p className="mt-1 text-muted-foreground" dir="auto">{c.how_to_measure}</p></details>
        </Card>
      )}
      <Card title={it("gr_bundles")}>
        {!d.bundles.length ? <p className="text-sm">{it("gr_no_bundles")}</p> : (
          <ul className="grid gap-2 text-sm">{d.bundles.map((b: any) => <li key={b.products.join("+")} className="rounded-lg bg-muted/50 p-2" dir="auto">{b.text}</li>)}</ul>
        )}
      </Card>
    </div>
  );
}

/** Per-business privacy: AI consent (with exactly what is / isn't sent) and erase-everything. */
export function Privacy() {
  const it = useIT();
  const { lang } = useApp();
  const qc = useQueryClient();
  const nav = useNavigate();
  const onErr = useErrorToast();
  const [ask, setAsk] = useState(false);
  const q = useQuery({ queryKey: ["ai-settings"], queryFn: xapi.aiSettings });
  if (!q.data) return null;
  const s = q.data;
  return (
    <Card title={it("pv_title")}>
      <label className="flex items-center gap-2 text-sm font-medium">
        <input type="checkbox" checked={s.ai_enabled} onChange={(e) => xapi.setAi(e.target.checked).then(() => qc.invalidateQueries({ queryKey: ["ai-settings"] })).catch(onErr)} />
        {it("pv_ai_on")}
      </label>
      <p className="mt-1 text-xs text-muted-foreground">{it("pv_ai_note")}</p>
      {s.month_usage && <p className="mt-1 text-xs text-muted-foreground">{it("pv_month", { used: s.month_usage.used, cap: s.month_usage.cap })}</p>}
      <div className="mt-2 grid gap-2 text-xs sm:grid-cols-2">
        <div><p className="font-semibold">{it("pv_sent")}</p><ul className="list-disc ps-4">{(lang === "ar" ? s.sent_when_on_ar : s.sent_when_on).map((x: string) => <li key={x}>{x}</li>)}</ul></div>
        <div><p className="font-semibold">{it("pv_never")}</p><ul className="list-disc ps-4">{(lang === "ar" ? s.never_sent_ar : s.never_sent).map((x: string) => <li key={x}>{x}</li>)}</ul></div>
      </div>
      <div className="mt-3 text-end">
        <button className="text-xs text-danger underline" onClick={() => setAsk(true)}>{it("pv_erase")}</button>
      </div>
      <Confirm open={ask} danger text={it("pv_erase_confirm")} onNo={() => setAsk(false)}
        onYes={() => { setAsk(false); xapi.eraseAll().then(() => { toast.success(it("pv_erased")); qc.clear(); nav({ to: "/" }); }).catch(onErr); }} />
    </Card>
  );
}
