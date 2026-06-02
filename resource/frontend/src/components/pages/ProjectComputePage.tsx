import { ProjectSqlPushdownTable } from '../index';

export function ProjectComputePage() {
  return (
    <div className="page-fill">
      <div className="flex flex-col gap-6 flex-1 min-h-0">
        <ProjectSqlPushdownTable />
      </div>
    </div>
  );
}
