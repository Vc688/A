export const defaultProhibitedClaims = [
  "guaranteed",
  "best",
  "top lawyer",
  "specialist",
  "expert",
  "we will win",
  "you have a case",
  "you need a lawyer",
  "results guaranteed",
  "you are entitled",
  "the law requires you to",
  "your claim is valid",
  "you will recover",
  "we can solve your legal problem"
];

export function findProhibitedClaims(text: string, dictionary = defaultProhibitedClaims): string[] {
  const normalized = text.toLowerCase();
  return dictionary.filter((phrase) => normalized.includes(phrase.toLowerCase()));
}
