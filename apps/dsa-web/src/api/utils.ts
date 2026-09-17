import camelcaseKeys from 'camelcase-keys';

/**
 * Convert snake_case object keys to camelCase
 * @param data API response payload (snake_case)
 * @returns the converted camelCase object
 */
export function toCamelCase<T>(data: unknown): T {
    if (data === null || data === undefined) {
        return data as T;
    }
    return camelcaseKeys(data as Record<string, unknown>, { deep: true }) as T;
}
