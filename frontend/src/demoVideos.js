// Preloaded demo links for the Check a video strip. Expected outcomes assume
// a typical "science yes / gaming ask / scary or adversarial block" policy —
// real results still come from the live classifier.
export const DEMO_VIDEOS = [
  {
    id: "e8YzKyot4Pc",
    label: "Solar system",
    expected: "allow",
    note: "Clean educational",
  },
  {
    id: "CbTaDOuSePk",
    label: "Apollo 11",
    expected: "allow",
    note: "Clean educational",
  },
  {
    id: "ndDpjT0_IM0",
    label: "How the brain works",
    expected: "allow",
    note: "Clean educational",
  },
  {
    id: "MnWJi64MDcc",
    label: "Minecraft survival",
    expected: "ask_parent",
    note: "Gaming — usually Ask parent",
  },
  {
    id: "Hxcs5g1AsZQ",
    label: "Prank scare",
    expected: "ask_parent",
    note: "Prank — usually Ask parent",
  },
  {
    id: "gEVCXrjvaT4",
    label: "Scary Minecraft myths",
    expected: "block",
    note: "Scary themes",
  },
  {
    id: "Lx_jIEBFxek",
    label: "Minecraft prank battle",
    expected: "block",
    note: "Gaming + prank mix",
  },
  {
    id: "QF5YpZI1wnk",
    label: "Adversarial example",
    expected: "block",
    note: "Known limitation — innocuous title, risky content",
    adversarial: true,
  },
];

export function demoUrl(videoId) {
  return `https://www.youtube.com/watch?v=${videoId}`;
}
