// The Jev request format: the JavaScript twin of jev_core/__init__.py.
// Both read jev_core/prompts.json and are tested against jev_core/fixtures.json,
// so the cloud asks TypeSafe exactly what the client's direct mode would.

import prompts from "../../jev_core/prompts.json" with { type: "json" };

export const COURSES = prompts.courses;
export const LETTERS = prompts.letters;
export const LIMITS = prompts.limits;
export const LETTER_CHOICES = Object.keys(LETTERS.criteria);

export class JevError extends Error {}

/** How a course is cached, locally and here: "CODE|title". */
export function courseKey(code, title) {
  return `${code.trim().toUpperCase()}|${title.trim().toLowerCase()}`;
}

/** One noul question per course; the state holds category, definition, course_01, ... */
export function courseBody(model, category, definition, courses) {
  const width = Math.max(2, String(courses.length).length);
  const state = { category, definition };
  const questions = {};
  courses.forEach(([code, title], i) => {
    const n = String(i + 1).padStart(width, "0");
    const key = `course_${n}`;
    state[key] = `${code} - ${title}`;
    questions[`q${n}`] = {
      type: "noul",
      instructions: COURSES.instructions.replace("{key}", key),
      criteria: COURSES.criteria,
    };
  });
  return { model, state, questions };
}

/** One 0-1 score per question of `body`, in order. */
export function parseScores(body, reply) {
  const answers = (reply && reply.answers) || {};
  return Object.keys(body.questions).map((q) => {
    const score = Number(answers[q] && answers[q].noul);
    if (answers[q] == null || answers[q].noul == null || !Number.isFinite(score)) {
      throw new JevError(`unexpected response: ${JSON.stringify(reply).slice(0, 300)}`);
    }
    return score;
  });
}

export function letterBody(model, letter) {
  return {
    model,
    state: { letter },
    questions: { rank: { type: "choice", instructions: LETTERS.instructions, criteria: LETTERS.criteria } },
  };
}

/** [excellent|good|normal, confidence] */
export function parseChoice(reply) {
  const answer = ((reply && reply.answers) || {}).rank || {};
  if (!LETTER_CHOICES.includes(answer.choice)) {
    throw new JevError(`unexpected answer: ${JSON.stringify(answer).slice(0, 300)}`);
  }
  return [answer.choice, Number(answer.confidence) || 0];
}
