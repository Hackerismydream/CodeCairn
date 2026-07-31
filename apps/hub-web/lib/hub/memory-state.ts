export type RequestGate = {
  begin: () => number;
  invalidate: () => void;
  isCurrent: (token: number) => boolean;
};

export function createRequestGate(): RequestGate {
  let generation = 0;
  return {
    begin() {
      generation += 1;
      return generation;
    },
    invalidate() {
      generation += 1;
    },
    isCurrent(token) {
      return token === generation;
    },
  };
}

export function memoryFilterDisabled(
  loading: boolean,
  selecting: boolean,
  selected: boolean,
): boolean {
  return loading || selecting || selected;
}

export function memoryPaginationDisabled(
  loading: boolean,
  selecting: boolean,
  available: boolean,
): boolean {
  return loading || selecting || !available;
}

export function retryFromFirstPage(errorCode: string | undefined): boolean {
  return errorCode === "cursor_invalid";
}

export function isUnfilteredNamespaceEmpty(
  itemCount: number,
  typeFilter: string,
  statusFilter: string,
  hasPageCursor: boolean,
): boolean {
  return (
    itemCount === 0 &&
    typeFilter === "all" &&
    statusFilter === "all" &&
    !hasPageCursor
  );
}
