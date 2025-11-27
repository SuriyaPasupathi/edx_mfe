/* eslint-disable import/prefer-default-export */
import { getConfig } from '@edx/frontend-platform';
import { getAuthenticatedHttpClient, getAuthenticatedUser } from '@edx/frontend-platform/auth';
import { logError, logInfo } from '@edx/frontend-platform/logging';

export const getNotices = async () => {
  // Check if notices are enabled before making API call
  if (!getConfig().ENABLE_NOTICES) {
    return null;
  }

  const authenticatedUser = getAuthenticatedUser();
  if (!authenticatedUser) {
    return null;
  }

  const url = new URL(`${getConfig().LMS_BASE_URL}/notices/api/v1/unacknowledged`);
  try {
    const { data } = await getAuthenticatedHttpClient().get(url.href, {});
    return data;
  } catch (e) {
    // we will just swallow error, as that probably means the notices app is not installed.
    // Notices are not necessary for the rest of courseware to function.
    const { customAttributes: { httpErrorStatus } } = e;
    if (httpErrorStatus === 404) {
      // Silently handle 404 - notices plugin is not installed
      // No need to log as this is expected when the plugin is not available
      return null;
    } else {
      logError(e);
    }
  }
  return null;
};
