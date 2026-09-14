export const buildPaginationState = (itemCount, pageSize, currentPage = 1) => {
  const safeItemCount = Math.max(0, Number(itemCount) || 0);
  const safePageSize = Math.max(1, Number(pageSize) || 1);
  const totalPages = Math.max(1, Math.ceil(safeItemCount / safePageSize));
  const safeCurrentPage = Math.min(
    Math.max(1, Number(currentPage) || 1),
    totalPages
  );

  return {
    totalPages,
    currentPage: safeCurrentPage,
    showPageSize: safeItemCount > 0,
    showNavigation: totalPages > 1,
  };
};
