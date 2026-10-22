import Link from "next/link";

import { StatusBadge } from "@/components/ui";
import { formatDateTime } from "@/lib/format";
import type { DocumentItem } from "@/lib/types";

export function DocumentTable({ tenantId, docs }: { tenantId: string; docs: DocumentItem[] }) {
  return (
    <div className="table-wrap">
      <table>
        <caption className="sr-only">Documents</caption>
        <thead>
          <tr>
            <th scope="col">File</th>
            <th scope="col">Type</th>
            <th scope="col">Company / person</th>
            <th scope="col">Date / period</th>
            <th scope="col">Status</th>
            <th scope="col">Received</th>
          </tr>
        </thead>
        <tbody>
          {docs.map((d) => (
            <tr key={d.id}>
              <td>
                <Link href={`/t/${tenantId}/documents/${d.id}`}>{d.standardized_filename ?? d.original_filename}</Link>
                {d.review_required && <span className="badge badge-warn"> review</span>}
              </td>
              <td>{d.document_type ?? "—"}</td>
              <td>{d.company_or_person ?? "—"}</td>
              <td>{d.date_or_period ?? "—"}</td>
              <td><StatusBadge status={d.status} /></td>
              <td>{formatDateTime(d.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
