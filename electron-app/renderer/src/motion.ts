// Keep departing grid items in their old slot while their siblings settle.
// DOM measurements occur only when an item leaves, never on pointer movement.
export function pinLeavingItem(element: Element) {
  const item = element as HTMLElement;
  const { offsetWidth, offsetHeight, offsetLeft, offsetTop } = item;
  Object.assign(item.style, {
    width: `${offsetWidth}px`, height: `${offsetHeight}px`,
    left: `${offsetLeft}px`, top: `${offsetTop}px`,
  });
}

export function clearPinnedItem(element: Element) {
  for (const property of ['width', 'height', 'left', 'top']) (element as HTMLElement).style.removeProperty(property);
}
