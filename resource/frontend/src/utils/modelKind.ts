import type { ProjectSavedModelRef } from '../types';

export function normalizeModelValue(value: string | undefined): string {
  return String(value || '').trim().toUpperCase();
}

export function modelKindLabel(model: ProjectSavedModelRef): string {
  const type = normalizeModelValue(model.type);
  const predictionType = normalizeModelValue(model.predictionType);
  if (type === 'CLUSTERING') return 'Clustering';
  if (predictionType === 'BINARY_CLASSIFICATION') return 'Binary classification';
  if (predictionType === 'MULTICLASS' || predictionType === 'MULTICLASS_CLASSIFICATION') return 'Multiclass';
  if (predictionType === 'REGRESSION') return 'Regression';
  if (predictionType === 'TIMESERIES_FORECAST' || predictionType === 'TIME_SERIES_FORECAST') return 'Time series forecast';
  if (type === 'PREDICTION') return 'Prediction';
  return 'Unknown';
}
