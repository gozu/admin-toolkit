import type { Project, Permission, ExtractedFiles } from '../types';

interface ProjectsResult {
  projects: Project[];
}

interface ProjectData {
  name?: string;
  owner?: string;
  versionTag?: {
    versionNumber?: number;
  };
  permissions?: Array<{
    group?: string;
    user?: string;
    [key: string]: unknown;
  }>;
}

export class ProjectsParser {
  private extractedFiles: ExtractedFiles;
  private projectFiles: string[];
  private log: (message: string) => void;

  constructor(
    extractedFiles: ExtractedFiles,
    projectFiles: string[],
    log: (message: string) => void
  ) {
    this.extractedFiles = extractedFiles;
    this.projectFiles = projectFiles;
    this.log = log;
  }

  parse(): ProjectsResult {
    const projects: Project[] = [];
    const sortedProjectFiles = [...this.projectFiles].sort();

    for (const projectFilePath of sortedProjectFiles) {
      try {
        const content = this.extractedFiles[projectFilePath];
        if (!content) {
          this.log(`Project file not found or empty: ${projectFilePath}`);
          continue;
        }

        const projectData: ProjectData = JSON.parse(content);
        const pathParts = projectFilePath.split('/');
        const projectKey = pathParts[pathParts.indexOf('projects') + 1];

        let projectName = projectData.name || projectKey;
        projectName = projectName.replace(/_/g, ' ');

        let versionNumber = 0;
        if (
          projectData.versionTag &&
          typeof projectData.versionTag.versionNumber === 'number'
        ) {
          versionNumber = projectData.versionTag.versionNumber;
        }

        const permissions: Permission[] = [];
        if (projectData.permissions && Array.isArray(projectData.permissions)) {
          for (const perm of projectData.permissions) {
            const permissionEntry: Permission = {
              type: perm.group ? 'Group' : 'User',
              name: perm.group || perm.user || 'Unknown',
              permissions: {},
            };

            for (const [key, value] of Object.entries(perm)) {
              if (key !== 'group' && key !== 'user') {
                permissionEntry.permissions[key] = value as boolean;
              }
            }

            permissions.push(permissionEntry);
          }
        }

        const project: Project = {
          key: projectKey,
          name: projectName,
          owner: projectData.owner || 'Unknown',
          permissions: permissions,
          versionNumber: versionNumber,
        };

        projects.push(project);
      } catch (error) {
        this.log(
          `Error parsing project ${projectFilePath}: ${
            error instanceof Error ? error.message : 'Unknown error'
          }`
        );
      }
    }

    return { projects };
  }
}
