import { useIntl } from '@edx/frontend-platform/i18n';
import { useContextId } from '../../../../data/hooks';

import { useModel } from '../../../../generic/model-store';

import CourseGradeFooter from './CourseGradeFooter';
import CourseGradeHeader from './CourseGradeHeader';
import GradeBar from './GradeBar';
import CreditInformation from '../../credit-information/CreditInformation';

import messages from '../messages';

const CourseGrade = () => {
  const intl = useIntl();
  const courseId = useContextId();

  const {
    creditCourseRequirements,
    gradesFeatureIsFullyLocked,
    gradesFeatureIsPartiallyLocked,
    gradingPolicy,
  } = useModel('progress', courseId);

  // Safely get gradeRange, default to empty object if not available
  const gradeRange = gradingPolicy?.gradeRange || {};
  const gradeRangeValues = Object.values(gradeRange);
  // Default passing grade to 60% if no grade range is configured
  const passingGrade = gradeRangeValues.length > 0
    ? Number((Math.min(...gradeRangeValues) * 100).toFixed(0))
    : 60;

  const applyLockedOverlay = gradesFeatureIsFullyLocked ? 'locked-overlay' : '';

  return (
    <section className="text-dark-700 my-4 rounded raised-card">
      {(gradesFeatureIsFullyLocked || gradesFeatureIsPartiallyLocked) && <CourseGradeHeader />}
      <div className={applyLockedOverlay} aria-hidden={gradesFeatureIsFullyLocked}>
        <div className="row w-100 m-0 p-4">
          <div className="col-12 col-sm-6 p-0 pr-sm-5.5">
            <h2>{creditCourseRequirements
              ? intl.formatMessage(messages.gradesAndCredit)
              : intl.formatMessage(messages.grades)}
            </h2>
            <p className="small">
              {intl.formatMessage(messages.courseGradeBody)}
            </p>
          </div>
          <GradeBar passingGrade={passingGrade} />
        </div>
        <div className="row w-100 m-0 px-4">
          <CreditInformation />
        </div>
        <CourseGradeFooter passingGrade={passingGrade} />
      </div>
    </section>
  );
};

export default CourseGrade;
