// The student's id for a session, kept in localStorage so a refresh rejoins the same seat.
const key = (sessionId: string) => `lectureloop:student:${sessionId}`;

export const studentStore = {
  get: (sessionId: string) => (typeof window === "undefined" ? null : localStorage.getItem(key(sessionId))),
  set: (sessionId: string, studentId: string) => localStorage.setItem(key(sessionId), studentId),
  clear: (sessionId: string) => localStorage.removeItem(key(sessionId)),
};
