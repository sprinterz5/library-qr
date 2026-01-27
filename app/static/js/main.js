// Tab switching navigation
document.addEventListener('DOMContentLoaded', () => {
    const navLinks = document.querySelectorAll('.header-nav-link');
    const sidebarLinks = document.querySelectorAll('.sidebar-link');
    const sidebar = document.getElementById('sidebar');
    const sections = document.querySelectorAll('.section');
    const subsections = document.querySelectorAll('.subsection');
    const homeSection = document.getElementById('home');
    const mainContent = document.querySelector('.main-content');
    const eventsSection = document.querySelector('.hero[style*="background: var(--bg-light)"]');

    // Switch to a specific tab
    function switchTab(tabId) {
        // Update nav links
        navLinks.forEach(link => link.classList.toggle('active', link.dataset.tab === tabId));

        // Handle home vs other sections
        if (tabId === 'home') {
            homeSection.style.display = 'block';
            mainContent.style.display = 'none';
            if (eventsSection) eventsSection.style.display = 'block';
        } else {
            homeSection.style.display = 'none';
            mainContent.style.display = 'flex';
            if (eventsSection) eventsSection.style.display = 'none';

            // Show/hide sections
            sections.forEach(section => section.classList.toggle('active', section.id === tabId));

            // Show/hide sidebar (only for borrowing)
            if (tabId === 'borrowing') {
                sidebar.classList.add('visible');
                switchSubsection('overview');
            } else {
                sidebar.classList.remove('visible');
            }
        }
    }

    // Nav link click handlers
    navLinks.forEach(link => {
        link.addEventListener('click', (e) => {
            e.preventDefault();
            switchTab(link.dataset.tab);
        });
    });

    // Switch to a specific subsection within borrowing
    function switchSubsection(subsectionId) {
        // Update sidebar links
        sidebarLinks.forEach(link => {
            const linkTarget = link.getAttribute('href').substring(1);
            link.classList.toggle('active', linkTarget === subsectionId);
        });

        // Show/hide subsections
        subsections.forEach(sub => sub.classList.toggle('active', sub.id === subsectionId));
    }

    // Sidebar link click handlers (switch subsections)
    sidebarLinks.forEach(link => {
        link.addEventListener('click', (e) => {
            e.preventDefault();
            const targetId = link.getAttribute('href').substring(1);
            switchSubsection(targetId);
        });
    });

    // Initialize: show home by default
    switchTab('home');
});
