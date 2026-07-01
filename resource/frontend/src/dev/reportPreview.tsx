/* Dev-only entry for report-preview.html — renders the health-check deck
   with fixture data. Not part of the production build. */
import { createRoot } from 'react-dom/client';
import '../index.css';
import { ReportOverlay } from '../components/ReportOverlay';
import { FIXTURE_PARSED, FIXTURE_REPORT } from './reportFixture';

createRoot(document.getElementById('root')!).render(
  <ReportOverlay reportData={FIXTURE_REPORT} parsedData={FIXTURE_PARSED} onClose={() => {}} />,
);
