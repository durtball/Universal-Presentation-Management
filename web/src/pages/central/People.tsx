import { useCallback, useEffect, useMemo, useState } from "react";
import { centralApi } from "../../api/central";
import type { PersonRecord } from "../../api/types";
import { DataTable, type Column } from "../../components/DataTable";
import { PageState } from "../../components/Feedback";
import { Page, Panel } from "../../components/Page";
import { useApi } from "../../hooks/useApi";
import { useSession } from "../../state/session";
import { AdminBoundary } from "./Shared";
import { DeletionDialog } from "../../components/DeletionDialog";
import { BulkPeopleDeletionDialog } from "../../components/BulkPeopleDeletionDialog";

const baseColumns: Column<PersonRecord>[] = [
  { key: "name", label: "Permanent person", value: (row) => row.display_name },
  { key: "title", label: "Title", value: (row) => row.professional_title },
  {
    key: "organization",
    label: "Organization",
    value: (row) => row.organization,
  },
  { key: "email", label: "Primary email", value: (row) => row.primary_email },
];
export function People() {
  const { csrfToken } = useSession();
  const api = useMemo(() => centralApi(csrfToken), [csrfToken]);
  const result = useApi((signal) => api.people(signal), [api]);
  const [deleting,setDeleting]=useState<PersonRecord>();
  const [showBulkDelete,setShowBulkDelete]=useState(false);
  const [bulkOperation,setBulkOperation]=useState<import("../../api/types").DeletionOperation>();
  useEffect(()=>{
    api.currentBulkPeopleDeletion().then(operation=>{
      if(operation && ["pending","running","retry_wait","failed"].includes(operation.status)) {
        setBulkOperation(operation); setShowBulkDelete(true);
      }
    }).catch(()=>undefined);
  },[api]);
  const refreshPeople=result.refresh;
  const bulkCompleted=useCallback(()=>refreshPeople(),[refreshPeople]);
  const columns=useMemo<Column<PersonRecord>[]>(()=>[...baseColumns,{key:"actions",label:"Actions",value:()=>"",
    render:(row)=><button className="button button--danger" type="button" onClick={()=>setDeleting(row)}>Delete Person</button>}],[]);
  return (
    <Page
      eyebrow="Identity"
      title="People"
      description="One durable Central identity follows a person across shows and event participation."
    >
      <AdminBoundary>
        <Panel
          title="Permanent identity boundary"
          description="Names are not identity keys. Permanent deletion requires dependency review and exact-name confirmation."
        >
          <p className="muted">
            Matching uses stable identifiers and administrator-confirmed
            reconciliation.
          </p>
          <button className="button button--danger" type="button"
            disabled={!result.data?.length} onClick={()=>{setBulkOperation(undefined);setShowBulkDelete(true);}}>
            Delete All
          </button>
          {!result.loading && !result.data?.length ? <p className="muted">There are no Permanent People to delete.</p> : null}
        </Panel>
        <PageState
          {...result}
          empty={(rows) => !rows.length}
          onRetry={result.refresh}
        >
          {(rows) => (
            <DataTable
              rows={rows}
              columns={columns}
              rowKey={(row) => row.person_id}
              label="People"
            />
          )}
        </PageState>
        {deleting ? <DeletionDialog kind="Permanent Person" name={deleting.display_name}
          load={()=>api.personDeletionImpact(deleting.person_id)}
          start={(confirmation)=>api.deletePerson(deleting.person_id,confirmation)}
          close={()=>{setDeleting(undefined);result.refresh();}}/> : null}
        {showBulkDelete ? <BulkPeopleDeletionDialog
          load={()=>api.bulkPeopleDeletionImpact()} start={confirmation=>api.deleteAllPeople(confirmation)}
          status={operationId=>api.deletionStatus(operationId)} initialOperation={bulkOperation}
          completed={bulkCompleted} close={()=>{setShowBulkDelete(false);setBulkOperation(undefined);result.refresh();}}/> : null}
      </AdminBoundary>
    </Page>
  );
}
